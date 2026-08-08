"""Spotify track search — never hang.

Design rules:
  1. Every API call has a hard wall-clock timeout (thread + requests timeout).
  2. HTTP 429 is handled with a short capped sleep (Retry-After), then skip.
  3. A single query failure never blocks the rest of the pipeline.
  4. Pagination and variant retries are bounded and cheap.

API constraints (many developer apps):
  - Search ``limit`` may be capped at **10**
  - ``/audio-features`` may return **403**
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

import spotipy
from spotipy.exceptions import SpotifyException

from chapterscore.config import get_settings
from chapterscore.exceptions import SpotifyAPIError
from chapterscore.models import LyricsPreference, SearchQuerySpec

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Spotify Web API: many apps reject search limit > 10.
_SEARCH_PAGE_SIZE = 10
_MAX_PAGES_DEFAULT = 2

# Timeouts (seconds) — overridable via settings
_DEFAULT_REQUEST_TIMEOUT = 10.0  # requests connect+read
_DEFAULT_HARD_TIMEOUT = 12.0  # wall-clock per API call
_DEFAULT_MAX_429_SLEEP = 3.0  # never sleep longer than this on 429
_DEFAULT_COLLECTION_BUDGET = 90.0  # whole track-collection phase


class SpotifyTimeoutError(SpotifyAPIError):
    """A single Spotify call exceeded the hard timeout."""


class SpotifyRateLimitError(SpotifyAPIError):
    """Spotify returned 429 / quota exceeded."""


@dataclass
class SearchSession:
    """Mutable session state shared across queries in one generate run."""

    started_at: float = field(default_factory=time.monotonic)
    budget_seconds: float = _DEFAULT_COLLECTION_BUDGET
    hard_timeout: float = _DEFAULT_HARD_TIMEOUT
    max_429_sleep: float = _DEFAULT_MAX_429_SLEEP
    rate_limited: bool = False
    consecutive_failures: int = 0
    queries_ok: int = 0
    queries_failed: int = 0
    queries_timed_out: int = 0
    queries_rate_limited: int = 0

    def remaining(self) -> float:
        return max(0.0, self.budget_seconds - (time.monotonic() - self.started_at))

    def budget_exhausted(self) -> bool:
        return self.remaining() <= 0.5

    def call_timeout(self) -> float:
        """Per-call timeout: min of hard timeout and remaining budget."""
        return max(1.0, min(self.hard_timeout, self.remaining()))


_SESSION: SearchSession | None = None


def start_search_session(
    *,
    budget_seconds: float | None = None,
    hard_timeout: float | None = None,
) -> SearchSession:
    """Begin a timed search session for one generate run."""
    global _SESSION
    settings = get_settings()
    _SESSION = SearchSession(
        budget_seconds=budget_seconds
        if budget_seconds is not None
        else getattr(settings, "chapterscore_spotify_collection_budget", _DEFAULT_COLLECTION_BUDGET),
        hard_timeout=hard_timeout
        if hard_timeout is not None
        else getattr(settings, "chapterscore_spotify_timeout", _DEFAULT_HARD_TIMEOUT),
        max_429_sleep=getattr(
            settings, "chapterscore_spotify_max_429_sleep", _DEFAULT_MAX_429_SLEEP
        ),
    )
    return _SESSION


def get_search_session() -> SearchSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = start_search_session()
    return _SESSION


def end_search_session() -> SearchSession | None:
    global _SESSION
    s = _SESSION
    _SESSION = None
    return s


def _call_with_timeout(fn: Callable[[], T], *, timeout: float, label: str = "spotify") -> T:
    """
    Run ``fn`` in a **daemon** worker thread with a hard wall-clock timeout.

    Why not ThreadPoolExecutor?
      - ``with ThreadPoolExecutor`` / ``shutdown(wait=True)`` blocks until the
        worker finishes — so a hung HTTP call would still hang the caller.
      - Non-daemon pool threads also block process exit.

    Daemon threads are abandoned on timeout; the caller always returns within
    ``timeout`` seconds (plus a few ms of scheduling overhead).
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised in caller
            box["error"] = exc

    thread = threading.Thread(
        target=runner,
        name=f"spotify-{label[:24]}",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        # Worker still running — abandon it (daemon will die with process).
        raise SpotifyTimeoutError(
            f"Spotify {label} timed out after {timeout:.1f}s.",
            hint="Skipping this request and continuing.",
        )

    if "error" in box:
        raise box["error"]
    return box["result"]  # type: ignore[return-value]


def _parse_retry_after(exc: SpotifyException) -> float:
    headers = exc.headers or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return 1.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


def _spotify_call(
    fn: Callable[[], T],
    *,
    label: str = "api",
    retries: int = 1,
    session: SearchSession | None = None,
) -> T:
    """
    Execute a Spotify API callable with timeout + minimal 429 handling.

    - At most ``retries`` re-attempts after a short 429 sleep.
    - Timeouts and 4xx (except 429) fail immediately.
    - Never sleeps more than ``max_429_sleep`` seconds.
    """
    session = session or get_search_session()
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        if session.budget_exhausted():
            raise SpotifyTimeoutError(
                "Spotify search budget exhausted.",
                hint="Proceeding with tracks collected so far.",
            )
        if session.rate_limited and attempt == 0 and retries == 0:
            raise SpotifyRateLimitError(
                "Spotify is rate-limiting this session.",
                hint="Skipping further searches.",
            )

        timeout = session.call_timeout()
        try:
            return _call_with_timeout(fn, timeout=timeout, label=label)
        except SpotifyTimeoutError:
            raise
        except SpotifyException as exc:
            last_exc = exc
            if exc.http_status == 429:
                wait = min(_parse_retry_after(exc), session.max_429_sleep)
                msg = str(exc).lower()
                quota = "quota" in msg
                logger.warning(
                    "Spotify 429 on %s (attempt %s, Retry-After≈%.1fs, quota=%s)",
                    label,
                    attempt + 1,
                    wait,
                    quota,
                )
                if quota or attempt >= retries:
                    session.rate_limited = True
                    raise SpotifyRateLimitError(
                        "Spotify rate limit / quota exceeded.",
                        hint="Wait ~60s and retry, or continue with partial results.",
                    ) from exc
                # One short sleep then retry once
                time.sleep(wait)
                continue
            if exc.http_status and exc.http_status >= 500:
                if attempt < retries:
                    time.sleep(0.5)
                    continue
                raise SpotifyAPIError(f"Spotify server error ({exc.http_status}): {exc}") from exc
            # 4xx other than 429 — no retry
            raise SpotifyAPIError(f"Spotify API error ({exc.http_status}): {exc}") from exc
        except Exception as exc:
            last_exc = exc
            # Network errors etc. — one quick retry max, no long sleeps
            if attempt < retries and "timeout" not in type(exc).__name__.lower():
                time.sleep(0.3)
                continue
            raise SpotifyAPIError(f"Spotify request failed: {exc}") from exc

    raise SpotifyAPIError(f"Spotify request failed after retries: {last_exc}") from last_exc


def build_search_string(
    spec: SearchQuerySpec,
    lyrics: LyricsPreference,
    *,
    use_genre_operator: bool = False,
) -> str:
    """Compose a Spotify search string (genre: opt-in)."""
    parts = [spec.query.strip()]
    if use_genre_operator:
        for g in (spec.genres or [])[:1]:
            g_clean = g.strip().lower().replace(" ", "-")
            if g_clean and g_clean not in parts[0].lower():
                parts.append(f"genre:{g_clean}")

    if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental:
        q = " ".join(parts).lower()
        if not any(
            k in q
            for k in (
                "instrumental",
                "soundtrack",
                "score",
                "ambient",
                "orchestral",
                "ost",
                "cinematic",
            )
        ):
            parts.append("instrumental")

    return " ".join(p for p in parts if p)


def search_string_variants(query: str) -> list[str]:
    """At most 2 variants: full query + shortened form."""
    q = query.strip()
    if not q:
        return []
    variants = [q]
    words = q.split()
    if len(words) > 3:
        variants.append(" ".join(words[:3]))
    if "genre:" in q.lower():
        variants.append(q.replace("genre:", "").replace("  ", " ").strip())
    # Dedupe, cap at 2 to avoid request storms
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
        if len(out) >= 2:
            break
    return out


def _search_page(
    sp: spotipy.Spotify,
    query: str,
    *,
    limit: int = _SEARCH_PAGE_SIZE,
    offset: int = 0,
    market: str | None = "US",
    session: SearchSession | None = None,
) -> list[dict[str, Any]]:
    limit = min(max(int(limit), 1), _SEARCH_PAGE_SIZE)

    def _do():
        kwargs: dict[str, Any] = {
            "q": query,
            "type": "track",
            "limit": limit,
            "offset": offset,
        }
        if market:
            kwargs["market"] = market
        result = sp.search(**kwargs)
        items = ((result or {}).get("tracks") or {}).get("items") or []
        return [t for t in items if t and t.get("id")]

    return _spotify_call(_do, label=f"search:{query[:40]}", retries=1, session=session)


def search_tracks(
    sp: spotipy.Spotify,
    query: str,
    *,
    limit: int | None = None,
    market: str | None = "US",
    session: SearchSession | None = None,
) -> list[dict[str, Any]]:
    """
    Search tracks with bounded pagination.

    Raises SpotifyTimeoutError / SpotifyRateLimitError / SpotifyAPIError on
    hard failures so callers can skip and continue.
    """
    session = session or get_search_session()
    settings = get_settings()
    desired = limit or settings.chapterscore_max_search_results
    desired = max(1, min(int(desired), 20))
    pages = min(_MAX_PAGES_DEFAULT, max(1, (desired + _SEARCH_PAGE_SIZE - 1) // _SEARCH_PAGE_SIZE))

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(pages):
        if session.budget_exhausted() or session.rate_limited:
            break
        offset = page * _SEARCH_PAGE_SIZE
        try:
            items = _search_page(
                sp,
                query,
                limit=_SEARCH_PAGE_SIZE,
                offset=offset,
                market=market,
                session=session,
            )
        except SpotifyAPIError as exc:
            # Invalid limit → try page size 5 once
            if "Invalid limit" in str(exc) or (hasattr(exc, "message") and "400" in str(exc)):
                try:
                    items = _search_page(
                        sp, query, limit=5, offset=offset, market=market, session=session
                    )
                except SpotifyAPIError:
                    raise
            else:
                raise
        if not items:
            break
        for t in items:
            tid = t["id"]
            if tid not in seen:
                seen.add(tid)
                collected.append(t)
        if len(items) < _SEARCH_PAGE_SIZE or len(collected) >= desired:
            break
    return collected[:desired]


def search_tracks_resilient(
    sp: spotipy.Spotify,
    query: str,
    *,
    limit: int | None = None,
    market: str | None = "US",
    session: SearchSession | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Search with one simplified fallback. Never raises for soft failures —
    returns [] and logs so the caller can move on.

    Rate-limit is recorded on the session so later queries can short-circuit.
    """
    session = session or get_search_session()
    log = progress or (lambda _m: None)

    if session.budget_exhausted():
        log("⏱ Search budget exhausted — skipping remaining queries")
        return []
    if session.rate_limited:
        log("⚠ Spotify rate-limited — skipping remaining queries")
        return []

    variants = search_string_variants(query)
    last_err: str | None = None

    for i, variant in enumerate(variants):
        if session.budget_exhausted() or session.rate_limited:
            break
        try:
            results = search_tracks(
                sp, variant, limit=limit, market=market, session=session
            )
            if results:
                session.queries_ok += 1
                session.consecutive_failures = 0
                return results
            # Empty but successful — try next variant
            last_err = "0 results"
        except SpotifyTimeoutError as exc:
            session.queries_timed_out += 1
            session.queries_failed += 1
            session.consecutive_failures += 1
            last_err = str(exc.message)
            log(f"⏱ Timeout: {variant[:60]} — skipping")
            logger.warning("Search timeout for %r: %s", variant, exc)
            # Don't try more variants of a timed-out query — network is slow
            break
        except SpotifyRateLimitError as exc:
            session.queries_rate_limited += 1
            session.queries_failed += 1
            session.rate_limited = True
            last_err = str(exc.message)
            log(f"⚠ Rate limited on: {variant[:60]} — pausing further searches")
            logger.warning("Search rate-limited for %r: %s", variant, exc)
            break
        except SpotifyAPIError as exc:
            session.queries_failed += 1
            session.consecutive_failures += 1
            last_err = str(exc.message)
            log(f"✗ Search failed: {variant[:50]} ({exc.message[:80]})")
            logger.warning("Search failed for %r: %s", variant, exc)
            # Try simplified variant once if we have one
            if i + 1 < len(variants):
                continue
            break
        except Exception as exc:
            session.queries_failed += 1
            session.consecutive_failures += 1
            last_err = str(exc)
            log(f"✗ Unexpected search error: {variant[:50]}")
            logger.exception("Unexpected search error for %r", variant)
            break

    if last_err and not session.rate_limited:
        logger.debug("No results for %r (%s)", query, last_err)
    return []


_AUDIO_FEATURES_DISABLED = False


def get_audio_features(
    sp: spotipy.Spotify,
    track_ids: list[str],
    *,
    session: SearchSession | None = None,
) -> dict[str, dict[str, float]]:
    """Batch-fetch audio features; never hang; tolerate 403."""
    global _AUDIO_FEATURES_DISABLED
    if _AUDIO_FEATURES_DISABLED or not track_ids:
        return {}

    session = session or get_search_session()
    features_map: dict[str, dict[str, float]] = {}

    for i in range(0, len(track_ids), 100):
        if session.budget_exhausted():
            break
        chunk = track_ids[i : i + 100]

        def _do(c=chunk):
            return sp.audio_features(c)

        try:
            feats = _spotify_call(_do, label="audio-features", retries=0, session=session) or []
        except SpotifyTimeoutError as exc:
            logger.warning("audio_features timed out: %s", exc)
            return features_map
        except SpotifyRateLimitError as exc:
            logger.warning("audio_features rate-limited: %s", exc)
            return features_map
        except SpotifyAPIError as exc:
            msg = str(exc).lower()
            if "403" in msg or "401" in msg or "forbidden" in msg:
                logger.warning(
                    "audio_features unavailable (%s) — using heuristics only",
                    exc,
                )
                _AUDIO_FEATURES_DISABLED = True
                return {}
            logger.warning("audio_features error: %s", exc)
            return features_map
        except Exception as exc:
            logger.warning("audio_features error: %s", exc)
            return features_map

        for feat in feats:
            if not feat or not feat.get("id"):
                continue
            features_map[feat["id"]] = {
                k: float(feat[k])
                for k in (
                    "energy",
                    "valence",
                    "danceability",
                    "acousticness",
                    "instrumentalness",
                    "speechiness",
                    "liveness",
                    "tempo",
                )
                if feat.get(k) is not None
            }
    return features_map


def track_dict_to_base(track: dict[str, Any]) -> dict[str, Any]:
    artists = [a.get("name", "") for a in (track.get("artists") or []) if a.get("name")]
    album = (track.get("album") or {}).get("name") or ""
    external = (track.get("external_urls") or {}).get("spotify")
    return {
        "uri": track.get("uri") or f"spotify:track:{track['id']}",
        "id": track["id"],
        "name": track.get("name") or "Unknown",
        "artists": artists,
        "album": album,
        "popularity": int(track["popularity"]) if track.get("popularity") is not None else 0,
        "duration_ms": int(track.get("duration_ms") or 0),
        "explicit": bool(track.get("explicit")),
        "preview_url": track.get("preview_url"),
        "external_url": external,
    }
