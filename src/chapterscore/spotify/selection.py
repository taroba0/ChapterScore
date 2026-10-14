"""
End-to-end track selection with progressive fallback.

Design principles:
  - Hard reject podcasts / speech / commentary / non-music (all modes)
  - Hard reject dead-note / scale / drone / exercise tracks
  - Hard lyrics / instrumental constraint when selected
  - Composition-level de-dupe (one best version of each work)
  - Popularity quality floor (prefer fewer better tracks)
  - Duration (min_hours) is a serious target: keep searching in-style until
    ~88% of requested hours (or the in-style pool is exhausted)
  - Never pad with mismatched generic cinema to hit hours
  - Overall mode: cohesive, shuffle-friendly emotional world
  - Chapter mode: section progression, not minute-by-minute reading sync

Fallback stages (overall mode):
  1. Expanded vibe queries + STRICT instrumental filter
  2. Same candidate pool + MODERATE filter
  3. Broadened queries + RELAXED filter
  4. World-gated cinematic / pastoral bank
  5. In-style deep fill rounds (same style universe; duration-aware)
  6. Pastoral emergency only for realist worlds (never spectacle pad)

Chapter mode runs a lighter ladder per chapter, then in-style global fill
when still under the duration target.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Callable

import spotipy

from chapterscore.config import get_settings
from chapterscore.models import (
    BookVibeAnalysis,
    ChapterVibe,
    LyricsPreference,
    Mode,
    PersonalizationPrefs,
    RankedTrack,
    SearchQuerySpec,
    TasteStrength,
)
from chapterscore.spotify.personalization import (
    TasteProfile,
    build_taste_profile,
    recommendations_for_vibe,
    search_queries_for_personal_artists,
)
from chapterscore.spotify.queries import (
    allows_generic_cinematic_fallback,
    broaden_specs,
    cinematic_fallback_queries,
    expand_chapter_queries,
    expand_queries_from_analysis,
    in_style_deep_fill_queries,
    is_realist_literary_world,
    vibe_instrumental_queries,
    world_locked_queries,
)
from chapterscore.spotify.reference_playlists import harvest_reference_playlist_tracks
from chapterscore.spotify.ranking import (
    InstrumentalStrictness,
    apply_overall_cohesion,
    dedupe_tracks,
    drop_reading_band_outliers,
    filter_music_only,
    is_likely_instrumental,
    is_too_intense_for_reading,
    is_world_style_mismatch,
    passes_content_filter,
    passes_lyrics_filter,
    passes_popularity_filter,
    style_clash_near_kill,
    reading_safe_energy_target,
    score_track,
    select_diverse,
    smooth_chapter_playlist,
    smooth_playlist_order,
    total_duration_ms,
)
from chapterscore.spotify.search import (
    _CONSECUTIVE_FAIL_ABORT,
    _CONSECUTIVE_TIMEOUT_ABORT,
    build_search_string,
    end_search_session,
    get_audio_features,
    get_search_session,
    search_tracks_resilient,
    start_search_session,
    track_dict_to_base,
)

logger = logging.getLogger(__name__)
# Spotipy logs every HTTP error at ERROR — we handle those ourselves
logging.getLogger("spotipy").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)

ProgressCb = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _rank_raw(
    raw_tracks: list[dict],
    features: dict[str, dict[str, float]],
    spec: SearchQuerySpec,
    lyrics: LyricsPreference,
    *,
    matched_query: str,
    strictness: InstrumentalStrictness,
    chapter_number: int | str | None = None,
    vibe_note: str = "",
    artist_counts: Counter[str] | None = None,
    seen_ids: set[str] | None = None,
    taste: TasteProfile | None = None,
    from_recommendations: bool = False,
    analysis: BookVibeAnalysis | None = None,
) -> list[RankedTrack]:
    ranked: list[RankedTrack] = []
    exploration = taste.prefs.exploration if taste else 40
    settings = get_settings()
    # Quality floor: at least config default (~40); prefs may raise it further
    cfg_floor = settings.chapterscore_min_popularity
    pref_floor = taste.prefs.min_popularity if taste else cfg_floor
    min_pop = max(cfg_floor, pref_floor)
    suitable = analysis.style_keywords_good() if analysis else []
    avoid = analysis.style_keywords_bad() if analysis else []

    session = get_search_session()
    # When Spotify search/playlist payloads omit popularity consistently,
    # soft-pass unknown pop for this run (keep scoring / quality_penalty).
    if raw_tracks and not session.popularity_unavailable:
        missing = sum(
            1 for t in raw_tracks if t.get("popularity") is None
        )
        if missing >= max(1, int(0.8 * len(raw_tracks))):
            session.popularity_unavailable = True
            logger.info(
                "Spotify popularity field missing on %s/%s tracks — "
                "soft-passing unknown popularity for this session",
                missing,
                len(raw_tracks),
            )
    pop_unavailable = session.popularity_unavailable

    for t in raw_tracks:
        base = track_dict_to_base(t)
        feats = features.get(base["id"], {})
        track = RankedTrack(
            **base,
            features=feats,
            matched_query=matched_query,
            chapter_number=chapter_number,
            vibe_note=vibe_note,
        )
        track.is_instrumental = is_likely_instrumental(track)
        # Priority 0: hard content filter (speech / junk / dead-note / too-loud)
        if not passes_content_filter(track):
            continue
        if is_too_intense_for_reading(track):
            continue
        # Priority 0b: setting/era/world mismatch (F1 vs 1900s farm; Potter vs realist)
        if analysis and is_world_style_mismatch(
            track,
            intimacy_vs_epic=analysis.intimacy_vs_epic,
            realism_vs_dreaminess=analysis.realism_vs_dreaminess,
            era_feel=analysis.era_feel,
            setting_texture=analysis.setting_texture,
            avoid_styles=avoid,
            anti_generic_notes=list(analysis.anti_generic_notes or []),
            overall_mood=analysis.overall_mood,
            suitable_styles=suitable,
            book_title=analysis.book_title,
            authors=list(analysis.authors or []),
        ):
            continue
        # Priority 1: hard lyrics / instrumental constraint (outranks taste/recs/fallback)
        if not passes_lyrics_filter(track, lyrics, strictness=strictness):
            continue
        # Priority 1b: hard popularity floor — unknown pop needs strong catalogue
        # signal unless Spotify omitted popularity for this session (soft-pass).
        if min_pop > 0 and not passes_popularity_filter(
            track,
            min_pop,
            strict=True,
            popularity_unavailable=pop_unavailable,
        ):
            continue
        # Soft taste — never overrides content / lyrics / reading continuity / vibe
        affinity = taste.affinity_for_artists(track.artists) if taste else 0.0
        track.score = score_track(
            track,
            spec,
            lyrics,
            artist_counts=artist_counts,
            seen_ids=seen_ids,
            strictness=strictness,
            taste_affinity=affinity,
            exploration=exploration,
            min_popularity=min_pop,
            from_recommendations=from_recommendations,
            suitable_styles=suitable,
            avoid_styles=avoid,
            book_energy=analysis.overall_energy if analysis else spec.energy,
            atmospheres=list(analysis.atmospheres) if analysis else None,
            overall_mood=analysis.overall_mood if analysis else None,
            key_themes=list(analysis.key_themes) if analysis else None,
            intimacy_vs_epic=analysis.intimacy_vs_epic if analysis else None,
            narrative_voice=analysis.narrative_voice if analysis else None,
            distinctive_signature=analysis.distinctive_signature if analysis else None,
            setting_texture=analysis.setting_texture if analysis else None,
            dominant_tones=list(analysis.dominant_tones) if analysis else None,
            humor_level=analysis.humor_level if analysis else None,
            realism_vs_dreaminess=analysis.realism_vs_dreaminess if analysis else None,
            anti_generic_notes=list(analysis.anti_generic_notes) if analysis else None,
            vibe_keywords=analysis.vibe_keyword_pool() if analysis else None,
            era_feel=analysis.era_feel if analysis else None,
            pacing=analysis.pacing_profile or analysis.pacing if analysis else None,
        )
        # Hard-ish: avoid_styles clash near-kill — drop rather than pad playlist
        if track.score >= 0 and style_clash_near_kill(track, suitable=suitable, avoid=avoid):
            continue
        if track.score < 0:
            continue
        ranked.append(track)
    return ranked


def _search_pool(
    sp: spotipy.Spotify,
    specs: list[SearchQuerySpec],
    lyrics: LyricsPreference,
    *,
    strictness: InstrumentalStrictness,
    chapter_number: int | str | None = None,
    vibe_note: str = "",
    seen_ids: set[str] | None = None,
    artist_counts: Counter[str] | None = None,
    progress: ProgressCb = _noop,
    limit_per_query: int | None = None,
    label: str = "",
    early_stop_raw: int | None = None,
    taste: TasteProfile | None = None,
    analysis: BookVibeAnalysis | None = None,
) -> list[RankedTrack]:
    """Search specs, enrich features once, rank under a given strictness."""
    settings = get_settings()
    limit = limit_per_query or min(settings.chapterscore_max_search_results, 20)
    # Stop searching once we have a fat raw pool (saves Spotify quota)
    early_stop_raw = early_stop_raw if early_stop_raw is not None else 180
    all_raw: list[tuple[SearchQuerySpec, str, list[dict]]] = []
    total_hits = 0
    seen_raw_ids: set[str] = set()

    session = get_search_session()

    for idx, spec in enumerate(specs, start=1):
        if total_hits >= early_stop_raw:
            progress(f"Early-stop search at {total_hits} unique hits (quota-friendly)")
            break
        if session.budget_exhausted():
            progress(
                f"⏱ Collection budget hit ({session.budget_seconds:.0f}s) — "
                f"continuing with {total_hits} hits"
            )
            break
        if session.search_unreliable:
            progress("✗ Spotify search unreliable — aborting this stage")
            break
        if session.rate_limited:
            progress(f"⚠ Rate-limited — using {total_hits} hits gathered so far")
            break
        # Fail-fast: consecutive timeouts with 0 hits (don't burn budget on long queries)
        if (
            total_hits == 0
            and session.queries_timed_out >= _CONSECUTIVE_TIMEOUT_ABORT
            and session.consecutive_failures >= _CONSECUTIVE_TIMEOUT_ABORT
        ):
            progress(
                f"✗ {session.queries_timed_out} search timeouts with 0 hits — "
                "aborting this stage (fail-fast)"
            )
            session.mark_search_unreliable("consecutive timeouts with 0 hits")
            break
        # Bail if many consecutive failures (network down / API errors)
        if session.consecutive_failures >= _CONSECUTIVE_FAIL_ABORT and total_hits == 0:
            progress("✗ Multiple consecutive search failures — aborting this stage")
            session.mark_search_unreliable("consecutive search failures with 0 hits")
            break

        q = build_search_string(spec, lyrics, use_genre_operator=False)
        # After timeouts, shorten long vibe phrases before searching
        if session.queries_timed_out > 0:
            words = q.split()
            if len(words) > 3:
                short = " ".join(words[:3])
                if "instrumental" in q.lower() and "instrumental" not in short.lower():
                    short = f"{short} instrumental"
                q = short
        progress(f"Searching Spotify [{idx}/{len(specs)}]: {q}")
        # Never raises — failures are logged + skipped inside resilient search
        results = search_tracks_resilient(
            sp, q, limit=limit, session=session, progress=progress
        )
        if results:
            progress(f"  → {len(results)} hits")
        for t in results:
            tid = t.get("id")
            if tid and tid not in seen_raw_ids:
                seen_raw_ids.add(tid)
        total_hits = len(seen_raw_ids)
        all_raw.append((spec, q, results))

    ids = list(seen_raw_ids)

    features: dict[str, dict[str, float]] = {}
    if ids and not session.budget_exhausted():
        features = get_audio_features(sp, ids, session=session)
        if features:
            progress(f"Audio features loaded for {len(features)}/{len(ids)} tracks")
        else:
            progress("Audio features unavailable — ranking via title/artist/query heuristics")

    pool: list[RankedTrack] = []
    for spec, q, results in all_raw:
        pool.extend(
            _rank_raw(
                results,
                features,
                spec,
                lyrics,
                matched_query=q,
                strictness=strictness,
                chapter_number=chapter_number,
                vibe_note=vibe_note,
                artist_counts=artist_counts,
                seen_ids=seen_ids,
                taste=taste,
                analysis=analysis,
            )
        )

    # Dedupe by id keeping best score
    best: dict[str, RankedTrack] = {}
    for t in pool:
        prev = best.get(t.id)
        if prev is None or t.score > prev.score:
            best[t.id] = t
    pool = list(best.values())

    stage = f" [{label}]" if label else ""
    progress(
        f"Stage{stage}: {total_hits} raw hits → {len(pool)} after "
        f"{strictness.name.lower()} filter"
    )
    return pool


def _merge_unique(*pools: list[RankedTrack]) -> list[RankedTrack]:
    best: dict[str, RankedTrack] = {}
    for pool in pools:
        for t in pool:
            prev = best.get(t.id)
            if prev is None or t.score > prev.score:
                best[t.id] = t
    return list(best.values())


# When the user sets min_hours, keep searching until we reach this fraction.
_DURATION_FILL_RATIO = 0.88
# Count fill when hours are requested (serious, not 60% soft).
_COUNT_FILL_RATIO_WITH_HOURS = 0.85
# Count-only mode (no min_hours): slightly soft.
_COUNT_FILL_RATIO_COUNT_ONLY = 0.70


def _playlist_hours(tracks: list[RankedTrack]) -> float:
    return total_duration_ms(tracks) / 3_600_000


def _target_count(
    *,
    tracks_requested: int,
    min_tracks: int | None,
    min_hours: float | None,
) -> int:
    """Upper aim for track count derived from request + duration."""
    n = max(tracks_requested, min_tracks or 0)
    # Duration → track estimate (~3.5 min/track). Serious when hours are set.
    if min_hours and min_hours > 0:
        est = int((min_hours * 60) / 3.5) + 2
        n = max(n, est)
    # Allow long listening sessions (e.g. 3–6h) without an artificial 100 cap
    return min(n, 160)


def _quality_floor(lyrics: LyricsPreference) -> float:
    """Minimum score to include when quality-first soft targets apply."""
    if lyrics.normalized().is_instrumental_only:
        return 32.0
    return 22.0


def _should_search_more(
    tracks: list[RankedTrack],
    target: int,
    min_hours: float | None,
    *,
    quality_floor: float = 0.0,
) -> bool:
    """
    Whether to keep searching for more in-style candidates.

    When ``min_hours`` is set it is a **serious** duration target: keep
    searching until ~88% of requested hours (or the pool is exhausted by
    the caller). Never pad with out-of-style / generic cinema tracks —
    that gate lives in query selection, not here.
    """
    if not tracks:
        return True
    good = [t for t in tracks if t.score >= quality_floor] if quality_floor > 0 else tracks
    if not good:
        return True

    hours = _playlist_hours(good)

    if min_hours and min_hours > 0:
        # Duration is the primary stop condition
        if hours < min_hours * _DURATION_FILL_RATIO:
            return True
        # Still thin on count vs hour-derived target → keep going a bit
        need_n = max(8, int(target * _COUNT_FILL_RATIO_WITH_HOURS)) if target else 8
        if len(good) < need_n and hours < min_hours * 0.95:
            return True
        return False

    # Count-only mode
    soft_n = max(6, int(target * _COUNT_FILL_RATIO_COUNT_ONLY)) if target else 6
    return len(good) < soft_n


def format_duration_report(
    tracks: list[RankedTrack],
    min_hours: float | None,
) -> str:
    """Human-readable requested vs actual duration (for CLI/UI)."""
    actual = _playlist_hours(tracks)
    actual_min = total_duration_ms(tracks) / 60_000
    if min_hours and min_hours > 0:
        req_min = min_hours * 60.0
        pct = (actual / min_hours) * 100.0 if min_hours else 0.0
        if actual + 1e-9 < min_hours * _DURATION_FILL_RATIO:
            status = "under target — in-style pool may be exhausted"
        elif actual + 1e-9 < min_hours * 0.95:
            status = "near target"
        else:
            status = "on target"
        return (
            f"Requested ~{min_hours:g} h ({req_min:.0f} min) · "
            f"Actual {actual:.2f} h ({actual_min:.0f} min, {pct:.0f}%) · {status}"
        )
    return f"Actual duration {actual:.2f} h ({actual_min:.0f} min) · {len(tracks)} tracks"


def _pick_quality(
    pool: list[RankedTrack],
    target: int,
    *,
    max_per_artist: int,
    lyrics: LyricsPreference,
    book_energy: float | None = None,
    intimacy_vs_epic: float | None = None,
    cohesive: bool = False,
    smooth: bool = True,
    duration_fill: bool = False,
    min_hours: float | None = None,
) -> list[RankedTrack]:
    """
    Quality-first selection up to a soft target.

    Never pads with weak/jarring tracks. Applies reading-safe cohesion and
    adjacent-energy smoothing. Always de-duplicates.

    When ``duration_fill`` is True (requested hours still under target), use a
    wider energy band and a slightly lower score floor so we can reach ~hours
    without collapsing to a handful of tracks.
    """
    candidates = [
        t for t in pool if passes_content_filter(t) and not is_too_intense_for_reading(t)
    ]
    # Wider band when filling toward hours (or when audio-features are sparse —
    # handled inside drop_reading_band_outliers).
    band_override = 0.26 if duration_fill else None
    if cohesive:
        candidates = apply_overall_cohesion(
            candidates,
            book_energy=book_energy,
            intimacy_vs_epic=intimacy_vs_epic,
            max_energy_gap=band_override,
        )
        candidates = drop_reading_band_outliers(
            candidates,
            book_energy=book_energy,
            intimacy_vs_epic=intimacy_vs_epic,
            max_dev=band_override,
        )

    candidates = filter_music_only(candidates)
    floor = _quality_floor(lyrics)
    if duration_fill:
        floor = floor * 0.72
    chosen = select_diverse(
        candidates, target, max_per_artist=max_per_artist, min_score=floor
    )
    # Lower the score floor if still thin vs hour-derived target
    thin_bar = max(4, int(target * (0.45 if duration_fill else 0.30)))
    if len(chosen) < thin_bar:
        chosen = select_diverse(
            candidates,
            target,
            max_per_artist=max(max_per_artist, 3 if duration_fill else max_per_artist),
            min_score=floor * (0.50 if duration_fill else 0.65),
        )
    chosen = filter_music_only(dedupe_tracks(chosen))
    if cohesive:
        # Second pass: only hard-drop when NOT duration-filling; otherwise keep
        # the wider-band set so multi-hour requests are not re-collapsed.
        if duration_fill:
            chosen = drop_reading_band_outliers(
                chosen,
                book_energy=book_energy,
                intimacy_vs_epic=intimacy_vs_epic,
                max_dev=max(band_override or 0.26, 0.30),
            )
        else:
            chosen = drop_reading_band_outliers(
                chosen,
                book_energy=book_energy,
                intimacy_vs_epic=intimacy_vs_epic,
            )
    if smooth and chosen:
        chosen = smooth_playlist_order(chosen, drop_jarring=True)
    return chosen


def _max_per_artist(exploration: int) -> int:
    """Comfort allows more tracks per favorite artist; exploration spreads out."""
    if exploration <= 25:
        return 3
    if exploration <= 60:
        return 2
    return 1


def select_tracks_for_analysis(
    sp: spotipy.Spotify,
    analysis: BookVibeAnalysis,
    *,
    mode: Mode,
    lyrics: LyricsPreference,
    tracks_overall: int | None = None,
    tracks_per_chapter: int | None = None,
    min_tracks: int | None = None,
    min_hours: float | None = None,
    personalization: PersonalizationPrefs | None = None,
    progress: ProgressCb = _noop,
) -> list[RankedTrack]:
    """
    Select and order tracks with personalization + progressive fallback.

    Runs under a global wall-clock budget so generate can never hang forever.
    Individual query failures are skipped; stages continue with what they have.
    """
    settings = get_settings()
    n_overall = tracks_overall or settings.chapterscore_tracks_overall
    n_per_ch = tracks_per_chapter or settings.chapterscore_tracks_per_chapter
    min_tracks = min_tracks if min_tracks is not None else settings.chapterscore_min_tracks
    min_hours = min_hours if min_hours is not None else settings.chapterscore_min_hours
    prefs = personalization or PersonalizationPrefs()
    lyrics = lyrics.normalized()

    # Longer listening requests need more search wall-clock (still capped)
    base_budget = settings.chapterscore_spotify_collection_budget
    if min_hours and min_hours >= 2.0:
        # 2.4h ≈ need ~40+ keepers; give search room for playlist harvest + fill
        budget_seconds = min(720.0, base_budget * max(1.5, float(min_hours) / 1.2))
    elif min_hours and min_hours >= 1.0:
        budget_seconds = min(480.0, base_budget * 1.5)
    else:
        budget_seconds = base_budget
    session = start_search_session(
        budget_seconds=budget_seconds,
        hard_timeout=settings.chapterscore_spotify_timeout,
    )
    progress(
        f"Spotify search session: {session.hard_timeout:.0f}s/call, "
        f"{session.budget_seconds:.0f}s total budget"
        + (f" (scaled for min_hours={min_hours:g})" if min_hours and min_hours >= 1.0 else "")
    )
    progress(f"Lyrics policy: {lyrics.display_label} (hard filter when instrumental-only)")

    if lyrics.is_instrumental_only and prefs.taste_strength != TasteStrength.DISABLE:
        progress(
            "Note: many of your top artists have vocals, so results may be limited "
            "in Instrumental only mode (Top Artists still enabled as a soft seed)"
        )

    # Resolve personal taste once per run (may be empty if disabled)
    taste = build_taste_profile(sp, prefs, progress=progress)
    progress(
        f"Priority: (1) lyrics → (2) book style → (3) exploration={prefs.exploration} "
        f"→ (4) taste={prefs.taste_strength.value}; "
        f"recommendations={'on' if prefs.use_recommendations else 'off'}"
    )
    safe_e = reading_safe_energy_target(
        analysis.overall_energy,
        intimacy_vs_epic=analysis.intimacy_vs_epic,
    )
    if mode == Mode.OVERALL or not analysis.chapters:
        progress(
            f"Overall mode: reading companion · cohesive world · "
            f"safe energy target ~{safe_e:.2f} (shuffle-friendly)"
        )
    else:
        progress(
            f"Chapter mode: smoothed progression · reading-safe energy ~{safe_e:.2f}"
        )

    try:
        if mode == Mode.OVERALL or not analysis.chapters:
            result = _select_overall(
                sp,
                analysis,
                lyrics=lyrics,
                tracks_requested=n_overall,
                min_tracks=min_tracks,
                min_hours=min_hours,
                taste=taste,
                progress=progress,
            )
        else:
            result = _select_chapter(
                sp,
                analysis,
                lyrics=lyrics,
                tracks_per_chapter=n_per_ch,
                min_tracks=min_tracks,
                min_hours=min_hours,
                taste=taste,
                progress=progress,
            )
    finally:
        ended = end_search_session()
        if ended:
            progress(
                f"Search stats: ok={ended.queries_ok} failed={ended.queries_failed} "
                f"timeouts={ended.queries_timed_out} rate_limits={ended.queries_rate_limited} "
                f"elapsed={time.monotonic() - ended.started_at:.0f}s"
            )

    # Final safety: de-dupe + strip speech/non-music/too-loud, then smooth order
    cleaned = filter_music_only(
        [t for t in dedupe_tracks(result) if not is_too_intense_for_reading(t)]
    )
    if mode == Mode.CHAPTER and cleaned and any(t.chapter_number is not None for t in cleaned):
        cleaned = smooth_chapter_playlist(cleaned)
    elif cleaned:
        cleaned = smooth_playlist_order(cleaned, drop_jarring=True)
    progress(
        f"Reading companion: {len(cleaned)} tracks after continuity smoothing "
        f"(dropped jarring / over-loud leftovers)"
    )
    return cleaned


def _select_overall(
    sp: spotipy.Spotify,
    analysis: BookVibeAnalysis,
    *,
    lyrics: LyricsPreference,
    tracks_requested: int,
    min_tracks: int | None,
    min_hours: float | None,
    taste: TasteProfile,
    progress: ProgressCb,
) -> list[RankedTrack]:
    target = _target_count(
        tracks_requested=tracks_requested,
        min_tracks=min_tracks,
        min_hours=min_hours,
    )
    q_floor = _quality_floor(lyrics)
    progress(
        f"Selecting up to ~{target} tracks (quality + style first"
        f"; requested={tracks_requested}"
        + (
            f", min_hours={min_hours:g} — serious duration target"
            if min_hours and min_hours > 0
            else ""
        )
        + ")…"
    )

    session = get_search_session()
    max_art = _max_per_artist(taste.prefs.exploration)
    # Long sessions need slightly higher per-artist allowance inside the same style
    if min_hours and min_hours >= 2.0:
        max_art = max(max_art, 3)

    def _stage_ok() -> bool:
        return not session.budget_exhausted() and not session.search_unreliable

    def _finalize(pool: list[RankedTrack], *, label: str) -> list[RankedTrack]:
        # Probe without duration relax first; if hours still short, re-pick with fill mode
        chosen = _pick_quality(
            pool,
            target,
            max_per_artist=max_art,
            lyrics=lyrics,
            book_energy=analysis.overall_energy,
            intimacy_vs_epic=analysis.intimacy_vs_epic,
            cohesive=True,
            smooth=True,
            duration_fill=False,
            min_hours=min_hours,
        )
        if (
            min_hours
            and min_hours > 0
            and (
                not chosen
                or _playlist_hours(chosen) < min_hours * _DURATION_FILL_RATIO
                or len(chosen) < max(8, int(target * 0.5))
            )
        ):
            fill_pick = _pick_quality(
                pool,
                target,
                max_per_artist=max(max_art, 3),
                lyrics=lyrics,
                book_energy=analysis.overall_energy,
                intimacy_vs_epic=analysis.intimacy_vs_epic,
                cohesive=True,
                smooth=True,
                duration_fill=True,
                min_hours=min_hours,
            )
            if len(fill_pick) > len(chosen) or (
                fill_pick and _playlist_hours(fill_pick) > _playlist_hours(chosen)
            ):
                chosen = fill_pick
        if chosen:
            hrs = _playlist_hours(chosen)
            progress(
                f"✓ {label}: {len(chosen)} tracks "
                f"(≈ {hrs * 60:.0f} min / {hrs:.2f} h"
                + (
                    f"; aim {min_hours:g} h"
                    if min_hours and min_hours > 0
                    else f"; aim ~{target} tracks"
                )
                + "; reading-smoothed)"
            )
        return chosen

    pool: list[RankedTrack] = []

    # ── Stage 0: Spotify Recommendations (allowed with Top Artists + instrumental) ──
    # Instrumental-only still hard-filters vocals after recommendations return.
    if taste.prefs.use_recommendations and _stage_ok():
        progress("Stage 0 — Spotify Recommendations (taste + book vibe)")
        rec_strict = (
            InstrumentalStrictness.STRICT
            if lyrics.is_instrumental_only
            else InstrumentalStrictness.PERMISSIVE
        )
        rec_raw = recommendations_for_vibe(
            sp, analysis, lyrics, taste, limit=min(50, max(20, target * 2)), progress=progress
        )
        if rec_raw:
            ids = [t["id"] for t in rec_raw if t.get("id")]
            features = get_audio_features(sp, ids, session=session) if ids else {}
            vibe_spec = SearchQuerySpec(
                query=analysis.overall_mood or "reflective",
                energy=analysis.overall_energy,
                reason="recommendations",
            )
            rec_ranked = _rank_raw(
                rec_raw,
                features,
                vibe_spec,
                lyrics,
                matched_query="spotify:recommendations",
                strictness=rec_strict,
                taste=taste,
                from_recommendations=True,
                analysis=analysis,
            )
            pool = _merge_unique(pool, rec_ranked)
            progress(f"Stage [0/recs]: {len(rec_raw)} raw → {len(rec_ranked)} after filters")
            chosen0 = _finalize(pool, label="Stage 0")
            if chosen0 and not _should_search_more(
                chosen0, target, min_hours, quality_floor=q_floor
            ):
                return chosen0

    # Personal-artist searches (allowed under instrumental-only; vocals still filtered)
    personal_specs: list[SearchQuerySpec] = []
    if taste.enabled:
        for q in search_queries_for_personal_artists(taste, analysis, lyrics, max_artists=6):
            personal_specs.append(
                SearchQuerySpec(
                    query=q,
                    energy=analysis.overall_energy,
                    reason="personal-artist",
                )
            )

    # ── Stage 1: book-vibe first (always) ──────────────────────────────────
    long_session = bool(min_hours and min_hours >= 2.0)
    if lyrics.is_instrumental_only:
        progress(
            "Stage 1 — Book-vibe instrumental search "
            f"(mood={analysis.overall_mood!r}, energy={analysis.overall_energy:.2f}; "
            "strict no-vocals; cohesive overall world)"
            + (f"; filling toward {min_hours:g} h" if min_hours else "")
        )
        # Smoke probes first: short high-recall queries before long vibe phrases
        smoke_specs = [
            SearchQuerySpec(
                query="piano instrumental",
                energy=analysis.overall_energy,
                reason="smoke",
            ),
            SearchQuerySpec(
                query="ambient instrumental",
                energy=analysis.overall_energy,
                reason="smoke",
            ),
        ]
        primary_specs = list(smoke_specs)
        primary_specs.extend(
            vibe_instrumental_queries(analysis, max_queries=24 if long_session else 20)
        )
        for sq in expand_queries_from_analysis(
            analysis, lyrics, max_queries=14 if long_session else 10, cohesive_overall=True
        ):
            primary_specs.append(sq)
        if personal_specs:
            # Soft personal seeds after book vibe (instrumental-flavored)
            primary_specs = primary_specs + personal_specs[:4]
        strict1 = InstrumentalStrictness.STRICT
        # Larger raw pools when aiming for multi-hour playlists
        early1 = max(200 if long_session else 140, target * (10 if long_session else 9))
        limit_q = 40 if long_session else 30
    else:
        primary_specs = expand_queries_from_analysis(
            analysis, lyrics, max_queries=22 if long_session else 14, cohesive_overall=True
        )
        if personal_specs and taste.prefs.exploration <= 55:
            primary_specs = personal_specs[:6] + primary_specs
        elif personal_specs:
            primary_specs = personal_specs[:3] + primary_specs
        progress(f"Stage 1 — {len(primary_specs)} book-vibe + taste queries (cohesive overall)")
        strict1 = InstrumentalStrictness.PERMISSIVE
        # Long vocal requests (e.g. romance @ 2.4h) need the same per-query depth
        early1 = max(200 if long_session else 100, target * (9 if long_session else 6))
        limit_q = 40 if long_session else 30

    pool1 = _search_pool(
        sp,
        primary_specs,
        lyrics,
        strictness=strict1,
        progress=progress,
        label="1/vibe",
        early_stop_raw=early1,
        limit_per_query=limit_q,
        taste=taste,
        analysis=analysis,
    )
    pool = _merge_unique(pool, pool1)
    chosen = _finalize(pool, label="Stage 1")
    if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
        return chosen

    # Stage 1 timed out / failed with 0 raw hits — do not thrash Stages 2–6
    if session.search_unreliable and not pool1 and not chosen:
        progress(
            "✗ Stage 1 got 0 hits after search timeouts/failures — "
            "skipping remaining stages (incl. deep fill). "
            "Retry when Spotify responds, or use the CLI."
        )
        return []

    # ── Stage 1b: harvest tracks from diverse Spotify reference playlists ──
    # Widens the candidate universe beyond keyword track-search (cafe / piano /
    # guitar / R&B / country / soft pop / soundtrack / …) so book vibes diverge.
    # Runs early for any min_hours≥1.0 so long requests (e.g. 2.4h) get a fat pool
    # before later stages burn budget.
    def _ingest_reference_harvest(
        *,
        label: str,
        max_playlists: int,
        tracks_per_playlist: int,
        max_raw_tracks: int,
        max_pools: int = 6,
        max_queries: int | None = None,
    ) -> list[RankedTrack]:
        nonlocal pool
        if not _stage_ok() or session.rate_limited:
            return []
        ref_raw = harvest_reference_playlist_tracks(
            sp,
            analysis,
            lyrics,
            max_playlists=max_playlists,
            tracks_per_playlist=tracks_per_playlist,
            max_raw_tracks=max_raw_tracks,
            max_pools=max_pools,
            max_queries=max_queries,
            progress=progress,
            session=session,
        )
        if not ref_raw:
            return []
        ids = [t["id"] for t in ref_raw if t.get("id")]
        features = get_audio_features(sp, ids, session=session) if ids else {}
        ref_spec = SearchQuerySpec(
            query=analysis.overall_mood or "reading companion",
            energy=analysis.overall_energy,
            reason="reference-playlist",
        )
        ref_strict = (
            InstrumentalStrictness.MODERATE
            if lyrics.is_instrumental_only
            else InstrumentalStrictness.PERMISSIVE
        )
        ref_ranked = _rank_raw(
            ref_raw,
            features,
            ref_spec,
            lyrics,
            matched_query="spotify:reference-playlist",
            strictness=ref_strict,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, ref_ranked)
        progress(
            f"Stage [{label}]: {len(ref_raw)} raw → {len(ref_ranked)} after filters"
        )
        return ref_ranked

    if _stage_ok() and not session.rate_limited:
        need_refs = (not chosen) or _should_search_more(
            chosen, target, min_hours, quality_floor=q_floor
        )
        if need_refs or (min_hours and min_hours >= 1.0):
            progress(
                f"Stage 1b — reference playlist harvest "
                f"({len(chosen)}/~{target}"
                + (f", aim {min_hours:g} h" if min_hours else "")
                + ")"
            )
            # 2.4h ≈ ~40 keepers: harvest more playlists / tracks up front
            if long_session:
                ref_limit, per_pl, raw_cap, pools_n, q_n = 16, 45, 420, 7, 22
            elif min_hours and min_hours >= 1.0:
                ref_limit, per_pl, raw_cap, pools_n, q_n = 12, 40, 320, 6, 16
            else:
                ref_limit, per_pl, raw_cap, pools_n, q_n = 8, 30, 200, 5, 12
            _ingest_reference_harvest(
                label="1b/refs",
                max_playlists=ref_limit,
                tracks_per_playlist=per_pl,
                max_raw_tracks=raw_cap,
                max_pools=pools_n,
                max_queries=q_n,
            )
            chosen = _finalize(pool, label="Stage 1b")
            if chosen and not _should_search_more(
                chosen, target, min_hours, quality_floor=q_floor
            ):
                return chosen

    # ── Stage 2: relax instrumental threshold ─────────────────────────────
    if lyrics.is_instrumental_only and _stage_ok():
        progress(
            f"Stage 2 — relaxing instrumental uncertainty "
            f"({len(chosen)}/~{target} so far; still hard-blocking clear vocals)"
        )
        if not session.rate_limited:
            pool2 = _search_pool(
                sp,
                primary_specs[:6],
                lyrics,
                strictness=InstrumentalStrictness.MODERATE,
                progress=progress,
                label="2/moderate",
                early_stop_raw=60,
                taste=taste,
                analysis=analysis,
            )
            pool = _merge_unique(pool, pool2)
        chosen = _finalize(pool, label="Stage 2")
        if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
            return chosen

    # ── Stage 3: broaden queries ──────────────────────────────────────────
    broad: list[SearchQuerySpec] = []
    strictness3 = (
        InstrumentalStrictness.RELAXED
        if lyrics.is_instrumental_only
        else InstrumentalStrictness.PERMISSIVE
    )
    if _stage_ok() and not session.rate_limited:
        progress(f"Stage 3 — broadening queries ({len(chosen)}/~{target} so far)")
        broad = broaden_specs(primary_specs[:8], lyrics)[:10]
        pool3 = _search_pool(
            sp,
            broad,
            lyrics,
            strictness=strictness3,
            progress=progress,
            label="3/broad",
            early_stop_raw=100 if long_session else 60,
            limit_per_query=40 if long_session else None,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, pool3)
        chosen = _finalize(pool, label="Stage 3")
        if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
            return chosen

    # World gate: realist / intimate literary books must not use generic cinema
    allow_cinema = allows_generic_cinematic_fallback(analysis)
    realist = is_realist_literary_world(analysis)
    patience_world = realist or not allow_cinema

    # ── Stage 4: cinematic / soundtrack fallback bank (world-gated) ───────
    cinema: list[SearchQuerySpec] = []
    if _stage_ok() and not session.rate_limited:
        if patience_world:
            progress(
                f"Stage 4 — pastoral / world-locked expansion only "
                f"(skipping generic cinema; {len(chosen)}/~{target} so far, "
                f"≈ {_playlist_hours(chosen) * 60:.0f} min)"
            )
            cinema = list(world_locked_queries(analysis, lyrics, max_queries=10)) + list(
                cinematic_fallback_queries(analysis, lyrics, max_queries=10)
            )
        else:
            progress(
                f"Stage 4 — cinematic fallback bank ({len(chosen)}/~{target} so far)"
            )
            cinema = cinematic_fallback_queries(analysis, lyrics, max_queries=8)
        if cinema:
            pool4 = _search_pool(
                sp,
                cinema[:14],
                lyrics,
                strictness=strictness3,
                progress=progress,
                label="4/pastoral" if patience_world else "4/cinematic",
                early_stop_raw=140 if long_session else 80,
                limit_per_query=40 if long_session else None,
                taste=taste,
                analysis=analysis,
            )
            pool = _merge_unique(pool, pool4)
            chosen = _finalize(pool, label="Stage 4")
            if chosen and not _should_search_more(
                chosen, target, min_hours, quality_floor=q_floor
            ):
                progress(format_duration_report(chosen, min_hours))
                return chosen

    # ── Stage 5: in-style deep fill (same universe; no spectacle pad) ──────
    # Keep searching adjacent in-genre artists until duration is near min_hours
    # or the in-style pool stops growing. Also expands reference-playlist harvest
    # when hours are still short (more queries / playlists / track search).
    max_fill_rounds = 5 if (min_hours and min_hours >= 2.0) else 2
    prev_n = len(chosen)
    for fill_round in range(1, max_fill_rounds + 1):
        if not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
            break
        if session.search_unreliable:
            progress("Stage 5 — skipping deep fill (search unreliable / prior timeouts)")
            break
        if not _stage_ok() or session.rate_limited:
            progress("Stage 5 — stopping deep fill (search budget / rate limit)")
            break
        progress(
            f"Stage 5 — in-style deep fill round {fill_round}/{max_fill_rounds} "
            f"({len(chosen)}/~{target}, ≈ {_playlist_hours(chosen) * 60:.0f} min"
            + (f" / aim {min_hours:g} h" if min_hours else "")
            + "; same style band only)"
        )
        # Extra playlist harvest mid-fill when duration still short
        if (
            min_hours
            and min_hours >= 1.5
            and fill_round in (1, 3)
            and _playlist_hours(chosen) < min_hours * _DURATION_FILL_RATIO
        ):
            progress(
                f"Stage 5 — expanding reference pools for duration fill "
                f"(round {fill_round})"
            )
            _ingest_reference_harvest(
                label=f"5/refs-r{fill_round}",
                max_playlists=10 if long_session else 6,
                tracks_per_playlist=45 if long_session else 35,
                max_raw_tracks=280 if long_session else 160,
                max_pools=7 if long_session else 6,
                max_queries=18 if long_session else 12,
            )
        fill_specs = in_style_deep_fill_queries(
            analysis,
            lyrics,
            fill_round=fill_round,
            max_queries=18 if long_session else 14,
        )
        if not fill_specs and cinema:
            fill_specs = cinema[:8]
        if not fill_specs:
            break
        pool5 = _search_pool(
            sp,
            fill_specs,
            lyrics,
            strictness=InstrumentalStrictness.RELAXED
            if lyrics.is_instrumental_only
            else InstrumentalStrictness.PERMISSIVE,
            progress=progress,
            label=f"5/in-style-r{fill_round}",
            early_stop_raw=160 if long_session else 80,
            limit_per_query=40 if long_session else 30,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, pool5)
        chosen = _finalize(pool, label=f"Stage 5r{fill_round}")
        # Stop if the in-style pool is exhausted (no new keepers)
        if len(chosen) <= prev_n:
            progress(
                "Stage 5 — in-style pool not growing; stopping deep fill "
                "(will not pad with mismatched cinema)"
            )
            break
        prev_n = len(chosen)

    if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
        progress(format_duration_report(chosen, min_hours))
        return chosen

    # ── Stage 6: last ditch inside style universe (never generic cinema for realist)
    if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
        progress(format_duration_report(chosen, min_hours))
        return chosen

    if patience_world:
        progress(
            "Stage 6 — pastoral emergency only "
            "(generic cinematic orchestral soundtrack blocked for this world)"
        )
        emergency_specs = in_style_deep_fill_queries(
            analysis, lyrics, fill_round=3, max_queries=8
        ) or world_locked_queries(analysis, lyrics, max_queries=6)
        if not emergency_specs:
            emergency_q = (
                "pastoral americana instrumental"
                if lyrics.is_instrumental_only or lyrics.prefers_instrumental
                else "americana folk ballad"
            )
            emergency_specs = [SearchQuerySpec(query=emergency_q, reason="pastoral-emergency")]
    else:
        progress("Stage 6 — ultra-generic safety net")
        emergency_q = (
            "cinematic orchestral soundtrack"
            if lyrics.is_instrumental_only or lyrics.prefers_instrumental
            else "cinematic soundtrack"
        )
        emergency_specs = [SearchQuerySpec(query=emergency_q, reason="emergency")]

    if _stage_ok() and not session.rate_limited:
        pool6 = _search_pool(
            sp,
            emergency_specs,
            lyrics,
            strictness=InstrumentalStrictness.RELAXED
            if lyrics.is_instrumental_only
            else InstrumentalStrictness.PERMISSIVE,
            progress=progress,
            label="6/pastoral-emergency" if patience_world else "6/emergency",
            limit_per_query=50,
            early_stop_raw=100,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, pool6)
        chosen = _finalize(pool, label="Emergency")

    if chosen:
        progress(format_duration_report(chosen, min_hours))
        if min_hours and min_hours > 0 and _playlist_hours(chosen) < min_hours * _DURATION_FILL_RATIO:
            progress(
                "⚠ Shortfall: in-style catalogue exhausted before reaching requested duration. "
                "Quality + setting fit preserved (no mismatched cinema padding)."
            )
    else:
        progress("✗ No tracks found even with emergency fallback")
    return chosen


def _select_chapter(
    sp: spotipy.Spotify,
    analysis: BookVibeAnalysis,
    *,
    lyrics: LyricsPreference,
    tracks_per_chapter: int,
    min_tracks: int | None,
    min_hours: float | None,
    taste: TasteProfile,
    progress: ProgressCb,
) -> list[RankedTrack]:
    chapters: list[ChapterVibe] = analysis.chapters
    progress(
        f"Selecting ~{tracks_per_chapter} tracks × {len(chapters)} chapters "
        "with per-chapter fallback…"
    )
    max_art = _max_per_artist(taste.prefs.exploration)

    seen_ids: set[str] = set()
    artist_counts: Counter[str] = Counter()
    final: list[RankedTrack] = []
    global_pool: list[RankedTrack] = []

    base_strict = (
        InstrumentalStrictness.STRICT
        if lyrics.is_instrumental_only
        else InstrumentalStrictness.PERMISSIVE
    )

    for ch in chapters:
        specs = expand_chapter_queries(ch, lyrics, analysis=analysis, max_queries=8)
        pool = _search_pool(
            sp,
            specs,
            lyrics,
            strictness=base_strict,
            chapter_number=ch.chapter_number,
            vibe_note=ch.vibe_note,
            seen_ids=seen_ids,
            artist_counts=artist_counts,
            progress=progress,
            label=f"ch{ch.chapter_number}",
            taste=taste,
            analysis=analysis,
        )
        global_pool = _merge_unique(global_pool, pool)

        chosen = select_diverse(pool, tracks_per_chapter, max_per_artist=max_art)
        # Relax for this chapter if thin
        if len(chosen) < tracks_per_chapter and lyrics.is_instrumental_only:
            pool_r = _search_pool(
                sp,
                specs + broaden_specs(specs, lyrics)[:4],
                lyrics,
                strictness=InstrumentalStrictness.RELAXED,
                chapter_number=ch.chapter_number,
                vibe_note=ch.vibe_note,
                seen_ids=seen_ids,
                artist_counts=artist_counts,
                progress=progress,
                label=f"ch{ch.chapter_number}/relax",
                taste=taste,
                analysis=analysis,
            )
            global_pool = _merge_unique(global_pool, pool_r)
            chosen = select_diverse(
                _merge_unique(pool, pool_r), tracks_per_chapter, max_per_artist=max(max_art, 2)
            )

        for t in chosen:
            t.chapter_number = ch.chapter_number
            t.vibe_note = ch.vibe_note or t.vibe_note
            seen_ids.add(t.id)
            if t.artists:
                artist_counts[t.artists[0].lower()] += 1
        if not chosen:
            progress(f"  ⚠ Chapter {ch.chapter_number}: no tracks yet (will fill later)")
        final.extend(chosen)

    # Soft global fill only when severely short (quality first, no weak padding)
    target = _target_count(
        tracks_requested=max(len(final), tracks_per_chapter * max(len(chapters), 1)),
        min_tracks=min_tracks,
        min_hours=min_hours,
    )
    q_floor = _quality_floor(lyrics)
    if _should_search_more(final, target, min_hours, quality_floor=q_floor):
        progress(
            f"Global fill — playlist has {len(final)} tracks, aim ~{target}"
            + (f" / {min_hours:g} h" if min_hours else "")
            + " (quality + style first)"
        )
        unused = [t for t in global_pool if t.id not in seen_ids]
        extra = select_diverse(
            unused,
            max(0, target - len(final)),
            max_per_artist=max(max_art, 3) if (min_hours and min_hours >= 2) else 2,
            min_score=q_floor * 0.7,
        )
        for t in extra:
            if t.id in seen_ids:
                continue
            seen_ids.add(t.id)
            final.append(t)

    if _should_search_more(final, target, min_hours, quality_floor=q_floor):
        patience_world = is_realist_literary_world(analysis) or (
            not allows_generic_cinematic_fallback(analysis)
        )
        if patience_world:
            progress(
                "Global fill — in-style deep fill only "
                "(generic cinema blocked)"
            )
            cinema = list(world_locked_queries(analysis, lyrics, max_queries=10))
            cinema += in_style_deep_fill_queries(analysis, lyrics, fill_round=1, max_queries=10)
            cinema += in_style_deep_fill_queries(analysis, lyrics, fill_round=2, max_queries=10)
            fill_label = "global/pastoral"
        else:
            progress("Global fill — cinematic bank (only if still thin)")
            cinema = cinematic_fallback_queries(analysis, lyrics, max_queries=12)
            fill_label = "global/cinematic"
        if cinema:
            pool_c = _search_pool(
                sp,
                cinema,
                lyrics,
                strictness=InstrumentalStrictness.RELAXED
                if lyrics.is_instrumental_only
                else InstrumentalStrictness.PERMISSIVE,
                seen_ids=seen_ids,
                progress=progress,
                label=fill_label,
                early_stop_raw=140 if (min_hours and min_hours >= 2) else 80,
                limit_per_query=30,
                taste=taste,
                analysis=analysis,
            )
            extra = select_diverse(
                [t for t in pool_c if t.id not in seen_ids],
                max(0, target - len(final)),
                max_per_artist=max(max_art, 3),
                min_score=q_floor * 0.55,
            )
            for t in extra:
                if t.id in seen_ids:
                    continue
                seen_ids.add(t.id)
                final.append(t)

    final = filter_music_only(dedupe_tracks(final))
    progress(
        f"Chapter mode assembled {len(final)} tracks "
        f"(aim ~{target}; progression by section, not minute-sync)"
    )
    progress(format_duration_report(final, min_hours))
    return final
