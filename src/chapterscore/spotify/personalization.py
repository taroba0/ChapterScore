"""Personal taste seeds + Spotify Recommendations for ChapterScore."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import spotipy
from spotipy.exceptions import SpotifyException

from chapterscore.models import BookVibeAnalysis, LyricsPreference, PersonalizationPrefs
from chapterscore.spotify.search import _spotify_call, get_search_session

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]


@dataclass
class TasteProfile:
    """Resolved personalization context for one generate run."""

    prefs: PersonalizationPrefs
    top_artists: list[dict[str, Any]] = field(default_factory=list)  # raw Spotify artist objects
    artist_ids: list[str] = field(default_factory=list)
    artist_names: list[str] = field(default_factory=list)
    # lowercase name → affinity 1.0 (rank 1) … ~0.3 (last)
    artist_affinity: dict[str, float] = field(default_factory=dict)
    seed_track_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.prefs.taste_strength.artist_limit() > 0 and bool(self.artist_ids)

    def affinity_for_artists(self, names: list[str]) -> float:
        """Best affinity among track artists (0 if none known)."""
        if not self.artist_affinity or not names:
            return 0.0
        best = 0.0
        for n in names:
            key = (n or "").strip().lower()
            if key in self.artist_affinity:
                best = max(best, self.artist_affinity[key])
        return best


def _noop(_: str) -> None:
    pass


def fetch_top_artists(
    sp: spotipy.Spotify,
    limit: int,
    *,
    time_range: str = "medium_term",
    progress: ProgressCb = _noop,
) -> list[dict[str, Any]]:
    """Fetch user's Spotify Top Artists. Returns [] on missing scope / empty."""
    if limit <= 0:
        return []
    limit = min(limit, 50)
    session = get_search_session()

    def _do():
        return sp.current_user_top_artists(limit=limit, time_range=time_range)

    try:
        data = _spotify_call(_do, label="top-artists", retries=0, session=session)
        items = (data or {}).get("items") or []
        progress(f"Loaded {len(items)} top artists from Spotify")
        return [a for a in items if a and a.get("id")]
    except Exception as exc:
        msg = str(exc).lower()
        if "403" in msg or "401" in msg or "insufficient" in msg or "scope" in msg:
            progress(
                "Could not load Top Artists (missing user-top-read scope). "
                "Re-login with Spotify to enable personalization."
            )
        else:
            progress(f"Top Artists unavailable ({exc}) — continuing without personal seeds")
        logger.warning("top artists failed: %s", exc)
        return []


def fetch_top_track_ids(
    sp: spotipy.Spotify,
    limit: int = 5,
    *,
    time_range: str = "medium_term",
) -> list[str]:
    """Optional seed tracks from user's top tracks."""
    if limit <= 0:
        return []
    session = get_search_session()

    def _do():
        return sp.current_user_top_tracks(limit=min(limit, 20), time_range=time_range)

    try:
        data = _spotify_call(_do, label="top-tracks", retries=0, session=session)
        items = (data or {}).get("items") or []
        return [t["id"] for t in items if t and t.get("id")]
    except Exception as exc:
        logger.debug("top tracks unavailable: %s", exc)
        return []


def build_taste_profile(
    sp: spotipy.Spotify,
    prefs: PersonalizationPrefs,
    *,
    progress: ProgressCb = _noop,
) -> TasteProfile:
    """Resolve top artists + affinity map according to user prefs."""
    profile = TasteProfile(prefs=prefs)
    n = prefs.taste_strength.artist_limit()
    if n <= 0:
        profile.notes.append("Personal taste disabled")
        progress("Personal taste: disabled")
        return profile

    progress(f"Using your Top {n} artists for personalization…")
    progress(f"Exploration level: {prefs.exploration} (0=comfort, 100=explore)")
    artists = fetch_top_artists(sp, n, progress=progress)
    profile.top_artists = artists
    profile.artist_ids = [a["id"] for a in artists]
    profile.artist_names = [a.get("name") or "" for a in artists]

    # Rank-based affinity: #1 → 1.0, last → ~0.35
    for i, name in enumerate(profile.artist_names):
        if not name:
            continue
        if len(profile.artist_names) <= 1:
            aff = 1.0
        else:
            aff = 1.0 - 0.65 * (i / (len(profile.artist_names) - 1))
        profile.artist_affinity[name.strip().lower()] = aff

    if profile.artist_names:
        preview = ", ".join(profile.artist_names[:5])
        more = f" +{len(profile.artist_names) - 5} more" if len(profile.artist_names) > 5 else ""
        progress(f"Taste seeds: {preview}{more}")
        profile.notes.append(f"Top artists: {preview}{more}")
    else:
        profile.notes.append("No top artists available")

    # A few top tracks help Recommendations seeds
    if prefs.use_recommendations and profile.artist_ids:
        profile.seed_track_ids = fetch_top_track_ids(sp, limit=3)

    return profile


def _target_features(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
) -> dict[str, float]:
    """Map book vibe → Spotify recommendation target_* params."""
    energy = float(analysis.overall_energy if analysis.overall_energy is not None else 0.5)
    # Valence from atmospheres (rough)
    atmospheres = {a.lower() for a in (analysis.atmospheres or [])}
    valence = 0.45
    if atmospheres & {"hopeful", "triumphant", "playful", "romantic"}:
        valence = 0.65
    if atmospheres & {"melancholic", "dark", "eerie", "solemn", "tense"}:
        valence = 0.3
    if atmospheres & {"melancholic", "dark"} and atmospheres & {"hopeful"}:
        valence = 0.4

    targets: dict[str, float] = {
        "target_energy": max(0.05, min(0.95, energy)),
        "target_valence": max(0.05, min(0.95, valence)),
    }
    mode = lyrics.normalized()
    if mode is LyricsPreference.INSTRUMENTAL_ONLY:
        targets["target_instrumentalness"] = 0.85
        targets["min_instrumentalness"] = 0.5
    elif mode is LyricsPreference.PREFER_INSTRUMENTAL:
        targets["target_instrumentalness"] = 0.65
    # ALLOW_LYRICS: no instrumental constraint
    return targets


def recommendations_for_vibe(
    sp: spotipy.Spotify,
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    profile: TasteProfile,
    *,
    limit: int = 40,
    progress: ProgressCb = _noop,
) -> list[dict[str, Any]]:
    """
    Call Spotify Recommendations API with personal + vibe seeds.

    Returns raw track dicts (may be empty if API unavailable).
    Spotify caps seed_* totals at 5 combined.
    """
    if not profile.prefs.use_recommendations:
        return []

    session = get_search_session()
    artist_seeds = profile.artist_ids[:3]
    track_seeds = profile.seed_track_ids[:2]
    # Fill remaining seed slots with genre-ish seeds via vibe (Spotify genre seeds are limited)
    # Use empty genres if we have enough artist/track seeds
    seed_count = len(artist_seeds) + len(track_seeds)
    genre_seeds: list[str] = []
    if seed_count < 5:
        # Map atmospheres to Spotify seed genres (subset of available seeds)
        genre_map = {
            "epic": "soundtracks",
            "adventurous": "soundtracks",
            "dark": "ambient",
            "eerie": "ambient",
            "calm": "ambient",
            "melancholic": "piano",
            "romantic": "classical",
            "tense": "soundtracks",
            "triumphant": "soundtracks",
            "mysterious": "ambient",
            "hopeful": "indie",
            "intimate": "singer-songwriter",
            "nostalgic": "folk",
            "playful": "indie-pop",
        }
        for atm in analysis.atmospheres or []:
            g = genre_map.get(atm.lower())
            if g and g not in genre_seeds:
                genre_seeds.append(g)
            if len(genre_seeds) + seed_count >= 5:
                break
        if not genre_seeds and seed_count == 0:
            genre_seeds = ["soundtracks", "ambient", "classical"][: 5 - seed_count]
        genre_seeds = genre_seeds[: max(0, 5 - seed_count)]

    if not artist_seeds and not track_seeds and not genre_seeds:
        progress("Recommendations skipped — no seeds available")
        return []

    targets = _target_features(analysis, lyrics)
    # Popularity floor from prefs
    min_pop = max(0, profile.prefs.min_popularity - 5)

    kwargs: dict[str, Any] = {
        "limit": min(limit, 100),
        "min_popularity": min_pop,
        **targets,
    }
    if artist_seeds:
        kwargs["seed_artists"] = artist_seeds
    if track_seeds:
        kwargs["seed_tracks"] = track_seeds
    if genre_seeds:
        kwargs["seed_genres"] = genre_seeds

    progress(
        "Calling Spotify Recommendations "
        f"(artists={len(artist_seeds)}, tracks={len(track_seeds)}, genres={genre_seeds})…"
    )

    def _do():
        return sp.recommendations(**kwargs)

    try:
        data = _spotify_call(_do, label="recommendations", retries=0, session=session)
        tracks = (data or {}).get("tracks") or []
        tracks = [t for t in tracks if t and t.get("id")]
        progress(f"Recommendations returned {len(tracks)} tracks")
        return tracks
    except Exception as exc:
        # API often 403/404 for restricted apps — fall back silently
        progress(f"Recommendations unavailable ({type(exc).__name__}) — using search fallback")
        logger.warning("recommendations failed: %s", exc)
        return []


def search_queries_for_personal_artists(
    profile: TasteProfile,
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_artists: int = 5,
) -> list[str]:
    """Keyword searches that blend top artists with book mood."""
    if not profile.artist_names:
        return []
    mood = (analysis.overall_mood or "cinematic").split()[0]
    # Under instrumental-only, bias personal seeds toward instrumental/score variants
    # (vocals are still hard-filtered downstream).
    instrumental_bias = lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental
    suffix = "instrumental" if instrumental_bias else ""
    queries = []
    for name in profile.artist_names[:max_artists]:
        q = f"{name} {mood} {suffix}".strip()
        queries.append(q)
        if instrumental_bias:
            queries.append(f"{name} soundtrack")
            queries.append(f"{name} instrumental")
    return queries[: max_artists * 2]
