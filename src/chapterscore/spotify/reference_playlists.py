"""Configurable Spotify *playlist* reference pools for wider discovery.

Track keyword search alone converges on the same popular instrumental artists
(Max Richter, Einaudi, etc.). Harvesting tracks from diverse editorial /
community playlists (cafe reads, piano, guitar, R&B, country, soft pop,
soundtracks, …) gives each book vibe a broader, more differentiated candidate
pool.

``REFERENCE_PLAYLIST_POOLS`` is a **starter map**, not exhaustive. Add or
retune families as new book vibes show thin or overlapping harvests. Pool
selection uses BookVibeAnalysis fields (styles, setting, era, intimacy,
moods, lyrics mode, realist vs cinematic) so East of Eden and a campus
romance should rarely share the same top playlist families.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import spotipy

from chapterscore.models import BookVibeAnalysis, LyricsPreference
from chapterscore.spotify.queries import (
    allows_generic_cinematic_fallback,
    is_realist_literary_world,
    literary_world_blob,
)
from chapterscore.spotify.search import (
    SearchSession,
    SpotifyAPIError,
    SpotifyRateLimitError,
    SpotifyTimeoutError,
    _spotify_call,
    get_search_session,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]


# ── Configurable vibe → playlist search phrases ─────────────────────────────
# Keys are pool families. Values are Spotify playlist-search queries.
# Prefer lists over one tiny hardcoded set so new families are easy to add.
# Starter map only — not an exhaustive catalogue of every vibe.

REFERENCE_PLAYLIST_POOLS: dict[str, list[str]] = {
    "cafe_reads": [
        "cafe reading playlist",
        "coffee shop acoustic",
        "bookstore cafe music",
        "quiet cafe jazz",
        "reading cafe vibes",
        "study cafe chill",
    ],
    "soft_pop": [
        "soft pop chill",
        "gentle indie pop",
        "bedroom pop soft",
        "feel good soft pop",
        "mellow pop vibes",
        "campus soft pop",
    ],
    "piano": [
        "solo piano reading",
        "peaceful piano instrumental",
        "neoclassical piano playlist",
        "study piano focus",
        "melancholy piano instrumental",
    ],
    "guitar": [
        "acoustic guitar instrumental",
        "fingerstyle guitar calm",
        "acoustic guitar reading",
        "soft acoustic guitar playlist",
        "classical guitar quiet",
    ],
    "rnb_soul": [
        "chill r&b playlist",
        "smooth soul reading",
        "quiet storm rnb",
        "neo soul chill",
        "soft r&b vibes",
    ],
    "country_americana": [
        "americana folk playlist",
        "alt country chill",
        "country acoustic ballads",
        "folk americana reading",
        "dusty country playlist",
    ],
    "soundtrack": [
        "film score instrumental",
        "movie soundtrack instrumental",
        "tv score calm",
        "emotional film score playlist",
        "indie film soundtrack",
        "fantasy adventure score quiet",
    ],
    "ambient_focus": [
        "ambient study focus",
        "lofi reading beats",
        "deep focus ambient",
        "calm atmospheric instrumental",
        "fantasy ambient quiet",
    ],
    "jazz_lounge": [
        "jazz lounge chill",
        "cool jazz reading",
        "nocturne jazz playlist",
        "soft jazz instrumental",
    ],
    "folk_indie": [
        "indie folk acoustic",
        "melancholic indie folk",
        "gentle folk playlist",
        "singer songwriter chill",
        "soft folk romance",
    ],
    "strings_chamber": [
        "chamber strings quiet",
        "string quartet calm",
        "cello piano intimate",
        "orchestral chamber reading",
    ],
    "pastoral_rural": [
        "pastoral americana instrumental",
        "rural folk instrumental",
        "countryside acoustic playlist",
        "documentary score pastoral",
    ],
    "dream_pop": [
        "dream pop ethereal",
        "shoegaze chill soft",
        "ethereal indie playlist",
    ],
    "electronic_chill": [
        "chill electronic ambient",
        "downtempo chill playlist",
        "soft synthwave chill",
    ],
}

# When instrumental-only, prefer these playlist-search phrases over vocal-heavy
# defaults for the same families (starter map; extend as needed).
_INSTRUMENTAL_POOL_QUERIES: dict[str, list[str]] = {
    "cafe_reads": [
        "cafe jazz instrumental",
        "coffee shop instrumental playlist",
        "bookstore ambient instrumental",
        "quiet cafe instrumental",
    ],
    "soft_pop": [
        "soft pop instrumental covers",
        "chill instrumental pop",
        "mellow instrumental playlist",
    ],
    "rnb_soul": [
        "r&b instrumental chill",
        "neo soul instrumental",
        "smooth soul instrumental",
    ],
    "country_americana": [
        "americana instrumental",
        "country acoustic instrumental",
        "folk americana instrumental",
    ],
    "folk_indie": [
        "indie folk instrumental",
        "acoustic folk instrumental calm",
        "fingerstyle folk instrumental",
    ],
    "dream_pop": [
        "dream pop instrumental",
        "ethereal ambient instrumental",
    ],
    "soundtrack": [
        "film score instrumental",
        "fantasy score instrumental quiet",
        "emotional film score playlist",
        "adventure score calm instrumental",
    ],
    "ambient_focus": [
        "ambient study focus",
        "fantasy ambient instrumental",
        "calm atmospheric instrumental",
        "deep focus ambient",
    ],
}


def _blob_hits(blob: str, keys: tuple[str, ...]) -> int:
    return sum(1 for k in keys if k in blob)


def _is_romance_campus_ya(analysis: BookVibeAnalysis, blob: str) -> bool:
    """Contemporary romance / campus / YA-NA — cafe + soft pop / R&B / folk."""
    # Strip negated contrast phrases so "not campus romance" does not trigger
    clean = blob
    for neg in (
        "not campus romance",
        "not a romance",
        "not romance",
        "no romance",
        "not romantic",
        "unlike romance",
        "not ya",
        "not young adult",
    ):
        clean = clean.replace(neg, " ")

    romance_keys = (
        "contemporary romance",
        "sports romance",
        "new adult",
        "young adult",
        "enemies to lovers",
        "friends to lovers",
        "slow burn",
        "rom-com",
        "romcom",
        "love story",
        "romantic",
        "romance",
        "campus",
        "college",
        "university",
        "hockey romance",
        "dorm",
        "locker room",
    )
    styles = " ".join(analysis.suitable_styles or []).lower()
    genres = " ".join(analysis.suggested_genres or []).lower()
    themes = " ".join(analysis.key_themes or []).lower()
    title = (analysis.book_title or "").lower().strip()
    intimacy = analysis.intimacy_vs_epic if analysis.intimacy_vs_epic is not None else 0.5
    dream = analysis.realism_vs_dreaminess if analysis.realism_vs_dreaminess is not None else 0.4

    # Exact known titles (starter map for thin-pool campus romance cases)
    if title in {"the deal", "the mistake", "the score", "the goal"}:
        return True

    positive = _blob_hits(clean, romance_keys)
    style_hit = any(
        k in styles or k in genres
        for k in ("romance", "soft pop", "indie pop", "chill r&b", "r&b", "neo soul", "cafe")
    )
    theme_hit = any(k in themes for k in ("romance", "college", "campus", "dating"))

    if positive >= 2:
        return True
    if positive >= 1 and (style_hit or theme_hit or intimacy >= 0.6):
        return True
    if style_hit and theme_hit and intimacy >= 0.55 and dream <= 0.55:
        return True
    if style_hit and intimacy >= 0.55 and dream <= 0.55 and any(
        k in clean for k in ("campus", "college", "romantic", "flirty", "enemies to lovers")
    ):
        return True
    return False


def _is_epic_fantasy_spectacle(analysis: BookVibeAnalysis, blob: str) -> bool:
    fantasy_keys = (
        "epic fantasy",
        "high fantasy",
        "quest fantasy",
        "dragon",
        "wizard",
        "sorcer",
        "magic",
        "mythic",
        "sword",
        "kingdom",
        "empire war",
        "battle",
        "space opera",
        "dark academia fantasy",
    )
    intimacy = analysis.intimacy_vs_epic if analysis.intimacy_vs_epic is not None else 0.5
    dream = analysis.realism_vs_dreaminess if analysis.realism_vs_dreaminess is not None else 0.4
    if intimacy <= 0.4 and dream >= 0.45 and _blob_hits(blob, fantasy_keys) >= 1:
        return True
    if _blob_hits(blob, fantasy_keys) >= 2 and intimacy <= 0.5:
        return True
    return False


def _queries_for_pool(pool: str, *, instrumental: bool) -> list[str]:
    """Return playlist-search phrases for a pool, preferring instrumental variants."""
    if instrumental and pool in _INSTRUMENTAL_POOL_QUERIES:
        # Instrumental variants first, then non-vocal-leaning defaults from the pool
        base = list(_INSTRUMENTAL_POOL_QUERIES[pool])
        for q in REFERENCE_PLAYLIST_POOLS.get(pool, []):
            ql = q.lower()
            if any(
                k in ql
                for k in (
                    "instrumental",
                    "score",
                    "piano",
                    "ambient",
                    "guitar",
                    "chamber",
                    "strings",
                    "focus",
                    "lofi",
                    "lo-fi",
                )
            ):
                if q not in base:
                    base.append(q)
        return base or list(REFERENCE_PLAYLIST_POOLS.get(pool, []))
    return list(REFERENCE_PLAYLIST_POOLS.get(pool, []))


def reference_pools_for_analysis(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_pools: int = 6,
) -> list[str]:
    """
    Pick distinct playlist-pool families for this book.

    Different literary worlds should pull from different families so two books
    with similar energy do not harvest the same three piano playlists.

    Uses styles, setting, era, intimacy, moods, lyrics mode, and realist vs
    cinematic signals. Pools are a starter map — extend ``REFERENCE_PLAYLIST_POOLS``
    when a vibe still under-differentiates.
    """
    lyrics = lyrics.normalized()
    inst = lyrics.is_instrumental_only or lyrics.prefers_instrumental
    instrumental_only = lyrics.is_instrumental_only
    blob = literary_world_blob(analysis)
    realist = is_realist_literary_world(analysis)
    allow_cinema = allows_generic_cinematic_fallback(analysis)
    intimacy = analysis.intimacy_vs_epic if analysis.intimacy_vs_epic is not None else 0.5
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    dream = analysis.realism_vs_dreaminess if analysis.realism_vs_dreaminess is not None else 0.4
    humor = analysis.humor_level if analysis.humor_level is not None else 0.3
    styles = " ".join(analysis.suitable_styles or []).lower()
    eras = (analysis.era_feel or "").lower()
    setting = (analysis.setting_texture or "").lower()
    moods = " ".join(
        [analysis.overall_mood or "", analysis.tone or ""]
        + list(analysis.atmospheres or [])
        + list(analysis.dominant_tones or [])
    ).lower()

    romance = _is_romance_campus_ya(analysis, blob)
    epic_fantasy = _is_epic_fantasy_spectacle(analysis, blob)

    scored: list[tuple[float, str]] = []

    def bump(pool: str, weight: float) -> None:
        if pool in REFERENCE_PLAYLIST_POOLS and weight > 0:
            scored.append((weight, pool))

    # ── Genre / vibe families (strong priors) ─────────────────────────────
    if romance:
        # Romance / campus / YA → cafe + soft pop / R&B / folk (not cinema pad)
        bump("cafe_reads", 2.6 if not instrumental_only else 1.4)
        bump("soft_pop", 2.4 if not instrumental_only else 0.9)
        bump("rnb_soul", 2.2 if not instrumental_only else 0.35)
        bump("folk_indie", 1.9 if not instrumental_only else 1.0)
        bump("guitar", 1.5)
        bump("piano", 1.2 if instrumental_only else 0.9)
        bump("dream_pop", 1.1 if dream >= 0.45 else 0.6)
        bump("jazz_lounge", 0.7)
        # Actively demote spectacle for romance
        bump("soundtrack", 0.25)
        bump("ambient_focus", 0.5 if instrumental_only else 0.35)

    elif realist or any(
        k in blob
        for k in ("pastoral", "rural", "farm", "americana", "dust", "valley", "midwest", "salinas")
    ):
        # Pastoral literary → pastoral / guitar / piano / chamber
        bump("pastoral_rural", 2.6)
        bump("guitar", 2.2)
        bump("piano", 2.0)
        bump("strings_chamber", 1.9)
        bump("country_americana", 1.8 if not instrumental_only else 1.1)
        bump("folk_indie", 1.6 if not instrumental_only else 1.0)
        bump("cafe_reads", 0.8 if not instrumental_only else 0.4)
        bump("ambient_focus", 0.9)
        bump("soundtrack", 0.35)  # documentary-adjacent only; keep low

    elif epic_fantasy or (
        allow_cinema
        and intimacy <= 0.4
        and any(k in blob for k in ("fantasy", "epic", "quest", "war", "mythic", "cinematic"))
    ):
        # Epic fantasy — soundtrack / ambient carefully (avoid cafe/R&B pad)
        bump("soundtrack", 2.4)
        bump("ambient_focus", 2.0)
        bump("strings_chamber", 1.4)
        bump("piano", 1.2)
        bump("electronic_chill", 1.0 if dream >= 0.5 else 0.5)
        bump("dream_pop", 0.9 if dream >= 0.55 else 0.4)
        bump("cafe_reads", 0.2)
        bump("rnb_soul", 0.1)
        bump("soft_pop", 0.1)

    else:
        # Generic reading companions (baseline before finer bumps)
        bump("cafe_reads", 1.2 if not instrumental_only else 0.55)
        bump("piano", 1.4 if instrumental_only or intimacy >= 0.5 else 0.8)
        bump("guitar", 1.1)
        bump("ambient_focus", 1.0 if energy <= 0.55 else 0.5)
        bump("strings_chamber", 1.0 if intimacy >= 0.45 else 0.4)
        if not instrumental_only and intimacy >= 0.5:
            bump("soft_pop", 0.9)
            bump("folk_indie", 1.0)

    # ── Setting / era / mood texture bumps ────────────────────────────────
    if any(k in eras or k in setting or k in blob for k in ("jazz", "noir", "lounge", "1920", "gatsby", "speakeasy")):
        bump("jazz_lounge", 2.2)

    if any(k in blob for k in ("soul", "r&b", "rnb", "motown", "gospel")) and not romance:
        bump("rnb_soul", 1.8 if not instrumental_only else 0.25)

    if any(k in blob for k in ("country", "nashville", "honky", "bluegrass")) and not epic_fantasy:
        bump("country_americana", 2.0)

    if dream >= 0.55 or any(k in blob for k in ("dream", "ethereal", "surreal")):
        bump("dream_pop", 1.6 if not instrumental_only else 0.8)

    if allow_cinema and not romance and not realist and any(
        k in blob for k in ("cinematic", "film", "score", "soundtrack", "epic", "war")
    ):
        bump("soundtrack", 1.8 if allow_cinema else 1.0)

    if not instrumental_only and any(k in blob for k in ("indie", "folk", "singer", "ballad")):
        bump("folk_indie", 1.5)

    if not instrumental_only and (humor >= 0.5 or energy >= 0.55) and not epic_fantasy:
        bump("rnb_soul", 0.85)
        bump("soft_pop", 0.9)
        bump("electronic_chill", 0.65)

    if any(k in blob for k in ("synth", "electronic", "cyber", "neon", "techno")):
        bump("electronic_chill", 1.9)

    # Mood / intimacy refinements
    if intimacy >= 0.65 and not epic_fantasy:
        bump("piano", 1.2)
        bump("strings_chamber", 1.1)
        bump("guitar", 0.9)
    if any(k in moods for k in ("melanchol", "bittersweet", "tender", "wistful")):
        bump("piano", 1.0)
        bump("folk_indie", 0.9 if not instrumental_only else 0.5)
    if any(k in moods for k in ("playful", "witty", "rom-com", "romcom", "light")):
        bump("soft_pop", 1.3 if not instrumental_only else 0.4)
        bump("cafe_reads", 1.0)

    # Book-specific style keywords → pools
    if "piano" in styles:
        bump("piano", 1.5)
    if "guitar" in styles or "acoustic" in styles:
        bump("guitar", 1.5)
    if "folk" in styles or "americana" in styles:
        bump("folk_indie", 1.3)
        bump("country_americana", 1.2)
    if "jazz" in styles:
        bump("jazz_lounge", 1.5)
    if "soundtrack" in styles or "score" in styles or "cinematic" in styles:
        bump("soundtrack", 1.4 if allow_cinema or epic_fantasy else 0.6)
    if "r&b" in styles or "soul" in styles or "rnb" in styles or "pop" in styles:
        bump("rnb_soul", 1.6 if not instrumental_only else 0.2)
        bump("soft_pop", 1.4 if not instrumental_only else 0.5)
    if "ambient" in styles or "lofi" in styles or "lo-fi" in styles:
        bump("ambient_focus", 1.5)
    if "chamber" in styles or "strings" in styles:
        bump("strings_chamber", 1.4)

    # Aggregate weights per pool, take top-N distinct
    weights: dict[str, float] = {}
    for w, pool in scored:
        weights[pool] = weights.get(pool, 0.0) + w

    # Instrumental-only: demote vocal-heavy pools hard
    if instrumental_only:
        for vocal_pool in ("rnb_soul", "country_americana", "folk_indie", "dream_pop", "soft_pop"):
            if vocal_pool in weights:
                weights[vocal_pool] *= 0.12
        # Prefer instrumental texture families already selected for this world
        if epic_fantasy:
            for inst_pool in ("soundtrack", "ambient_focus", "strings_chamber", "piano"):
                if inst_pool in weights:
                    weights[inst_pool] *= 1.25
            # Do not invent cafe/guitar/pastoral filler for mythic/epic worlds
            for demote in ("cafe_reads", "guitar", "pastoral_rural", "soft_pop", "rnb_soul"):
                if demote in weights:
                    weights[demote] *= 0.2
        elif romance:
            for inst_pool in ("piano", "guitar", "ambient_focus", "strings_chamber"):
                if inst_pool in weights:
                    weights[inst_pool] *= 1.15
        else:
            for inst_pool in ("piano", "guitar", "ambient_focus", "strings_chamber", "pastoral_rural"):
                if inst_pool in weights:
                    weights[inst_pool] *= 1.25
                elif realist:
                    weights[inst_pool] = weights.get(inst_pool, 0.0) + 0.8

    ordered = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    picked = [p for p, _ in ordered[:max_pools]]

    # Genre-specific guarantees
    if romance and not instrumental_only:
        for must in ("cafe_reads", "soft_pop", "rnb_soul"):
            if must not in picked and must in REFERENCE_PLAYLIST_POOLS:
                picked = ([must] + [p for p in picked if p != must])[:max_pools]
    if realist or any(k in blob for k in ("pastoral", "rural", "salinas")):
        for must in ("pastoral_rural", "guitar", "piano"):
            if must not in picked and must in REFERENCE_PLAYLIST_POOLS:
                picked = (picked + [must])[:max_pools]
    if epic_fantasy:
        for must in ("soundtrack", "ambient_focus"):
            if must not in picked and must in REFERENCE_PLAYLIST_POOLS:
                picked = ([must] + [p for p in picked if p != must])[:max_pools]

    # Guarantee at least one texture family
    if instrumental_only:
        if "piano" not in picked and "piano" in REFERENCE_PLAYLIST_POOLS:
            picked = (picked + ["piano"])[:max_pools]
    elif "cafe_reads" not in picked and intimacy >= 0.5 and "cafe_reads" in REFERENCE_PLAYLIST_POOLS:
        if not epic_fantasy:
            picked = (picked + ["cafe_reads"])[:max_pools]

    if not picked:
        if instrumental_only:
            picked = ["piano", "ambient_focus", "guitar"][:max_pools]
        else:
            picked = ["cafe_reads", "folk_indie", "soft_pop"][:max_pools]
    return picked


def playlist_search_queries_for_pools(
    pools: list[str],
    *,
    max_queries: int = 14,
    instrumental: bool = False,
) -> list[str]:
    """Flatten pool families into playlist search strings (deduped)."""
    out: list[str] = []
    seen: set[str] = set()
    for pool in pools:
        for q in _queries_for_pool(pool, instrumental=instrumental):
            key = " ".join(q.lower().split())
            if key and key not in seen:
                seen.add(key)
                out.append(q)
            if len(out) >= max_queries:
                return out
    return out


def reference_playlist_queries(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_queries: int = 14,
    max_pools: int = 6,
) -> list[tuple[str, str]]:
    """
    Return ``(pool_name, playlist_search_query)`` pairs for this book.

    Used by selection to harvest tracks from Spotify playlists.
    Instrumental-only mode prefers instrumental-leaning playlist queries.
    """
    lyrics = lyrics.normalized()
    instrumental = lyrics.is_instrumental_only or lyrics.prefers_instrumental
    pools = reference_pools_for_analysis(analysis, lyrics, max_pools=max_pools)
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pool in pools:
        for q in _queries_for_pool(pool, instrumental=instrumental):
            key = " ".join(q.lower().split())
            if key in seen:
                continue
            seen.add(key)
            pairs.append((pool, q))
            if len(pairs) >= max_queries:
                return pairs
    return pairs


def _search_playlists(
    sp: spotipy.Spotify,
    query: str,
    *,
    limit: int = 8,
    session: SearchSession | None = None,
) -> list[dict[str, Any]]:
    session = session or get_search_session()
    limit = min(max(int(limit), 1), 10)

    def _do():
        result = sp.search(q=query, type="playlist", limit=limit, market="US")
        items = ((result or {}).get("playlists") or {}).get("items") or []
        return [p for p in items if p and p.get("id")]

    try:
        return _spotify_call(_do, label=f"playlist-search:{query[:36]}", retries=1, session=session)
    except (SpotifyTimeoutError, SpotifyRateLimitError, SpotifyAPIError) as exc:
        logger.warning("Playlist search failed for %r: %s", query, exc)
        return []


def _playlist_tracks(
    sp: spotipy.Spotify,
    playlist_id: str,
    *,
    limit: int = 40,
    session: SearchSession | None = None,
) -> list[dict[str, Any]]:
    session = session or get_search_session()
    limit = min(max(int(limit), 1), 50)

    def _do():
        result = sp.playlist_items(
            playlist_id,
            fields="items(track(id,name,uri,artists,album,popularity,duration_ms,explicit,preview_url,external_urls))",
            limit=limit,
            additional_types=("track",),
        )
        items = (result or {}).get("items") or []
        tracks: list[dict[str, Any]] = []
        for it in items:
            t = (it or {}).get("track")
            if t and t.get("id") and t.get("type", "track") == "track":
                tracks.append(t)
        return tracks

    try:
        return _spotify_call(
            _do, label=f"playlist-items:{playlist_id[:12]}", retries=0, session=session
        )
    except (SpotifyTimeoutError, SpotifyRateLimitError, SpotifyAPIError) as exc:
        msg = str(exc).lower()
        if "403" in msg or "401" in msg or "forbidden" in msg:
            session.playlist_items_unavailable = True
            logger.warning(
                "playlist_items forbidden for %s (%s) — aborting further harvests",
                playlist_id,
                exc,
            )
        else:
            logger.warning("playlist_items failed for %s: %s", playlist_id, exc)
        return []


def harvest_reference_playlist_tracks(
    sp: spotipy.Spotify,
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_playlists: int = 10,
    tracks_per_playlist: int = 35,
    max_raw_tracks: int = 280,
    max_pools: int = 6,
    max_queries: int | None = None,
    progress: ProgressCb | None = None,
    session: SearchSession | None = None,
) -> list[dict[str, Any]]:
    """
    Search diverse vibe playlists and collect raw Spotify track dicts.

    Soft-fails: empty list on rate limit / budget / API errors.
    Raise ``max_playlists`` / ``max_raw_tracks`` for long ``min_hours`` fills.
    """
    log = progress or (lambda _m: None)
    session = session or get_search_session()
    q_cap = max_queries if max_queries is not None else max(8, max_playlists + 6)
    pairs = reference_playlist_queries(
        analysis,
        lyrics,
        max_queries=q_cap,
        max_pools=max_pools,
    )
    if not pairs:
        return []

    pools_used = sorted({p for p, _ in pairs})
    log(
        f"Reference playlists — pools: {', '.join(pools_used)} "
        f"({len(pairs)} search queries)"
    )

    collected: list[dict[str, Any]] = []
    seen_track: set[str] = set()
    seen_playlist: set[str] = set()
    playlists_used = 0
    # Pull more playlist candidates per query when filling long durations
    per_query_playlists = 3 if max_playlists >= 14 else 2

    for pool, query in pairs:
        if session.playlist_items_unavailable:
            break
        if session.budget_exhausted() or session.rate_limited:
            log("Reference playlists — stopping (budget / rate limit)")
            break
        if playlists_used >= max_playlists or len(collected) >= max_raw_tracks:
            break

        playlists = _search_playlists(sp, query, limit=min(8, 4 + per_query_playlists), session=session)
        if not playlists:
            continue

        # Prefer larger / more popular playlists when fields exist
        def _plist_key(p: dict[str, Any]) -> tuple:
            return (
                int(p.get("tracks", {}).get("total") or 0) if isinstance(p.get("tracks"), dict) else 0,
                int(p.get("followers", {}).get("total") or 0)
                if isinstance(p.get("followers"), dict)
                else 0,
            )

        playlists = sorted(playlists, key=_plist_key, reverse=True)

        for pl in playlists[:per_query_playlists]:
            if playlists_used >= max_playlists or len(collected) >= max_raw_tracks:
                break
            pid = pl.get("id")
            if not pid or pid in seen_playlist:
                continue
            seen_playlist.add(pid)
            name = (pl.get("name") or "")[:48]
            log(f"  ↓ Harvesting playlist [{pool}]: {name}")
            tracks = _playlist_tracks(
                sp, pid, limit=tracks_per_playlist, session=session
            )
            if session.playlist_items_unavailable:
                log(
                    "Reference playlists — playlist_items returned 403; "
                    "aborting Stage 1b harvest early"
                )
                break
            added = 0
            for t in tracks:
                tid = t.get("id")
                if not tid or tid in seen_track:
                    continue
                seen_track.add(tid)
                collected.append(t)
                added += 1
                if len(collected) >= max_raw_tracks:
                    break
            playlists_used += 1
            log(f"    → +{added} unique tracks (pool total {len(collected)})")

    log(
        f"Reference playlists done — {playlists_used} playlists → "
        f"{len(collected)} unique raw tracks"
    )
    return collected
