"""
End-to-end track selection with progressive fallback.

Fallback stages (overall mode):
  1. Expanded vibe queries + STRICT instrumental filter
  2. Same candidate pool + MODERATE filter
  3. Broadened queries + RELAXED filter
  4. Cinematic/soundtrack fallback bank + RELAXED filter
  5. Same bank + PERMISSIVE filter (almost never empty)

Chapter mode runs a lighter version of the same ladder per chapter,
then a global fill pass if the playlist is still thin.
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
    broaden_specs,
    cinematic_fallback_queries,
    expand_chapter_queries,
    expand_queries_from_analysis,
    vibe_instrumental_queries,
)
from chapterscore.spotify.ranking import (
    InstrumentalStrictness,
    apply_overall_cohesion,
    dedupe_tracks,
    is_likely_instrumental,
    passes_lyrics_filter,
    passes_popularity_filter,
    score_track,
    select_diverse,
    total_duration_ms,
)
from chapterscore.spotify.search import (
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
    min_pop = taste.prefs.min_popularity if taste else 0
    suitable = analysis.style_keywords_good() if analysis else []
    avoid = analysis.style_keywords_bad() if analysis else []
    # Soft popularity: only hard-filter when we have popularity data for most tracks
    pop_known = sum(1 for t in raw_tracks if (t.get("popularity") or 0) > 0)
    strict_pop = pop_known >= max(3, len(raw_tracks) // 2)

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
        # Priority 1: hard lyrics / instrumental constraint
        if not passes_lyrics_filter(track, lyrics, strictness=strictness):
            continue
        if min_pop > 0 and not passes_popularity_filter(
            track, min_pop, strict=strict_pop
        ):
            continue
        # Priority 4: soft taste (0 when instrumental-only disables tops)
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
        )
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
        if session.rate_limited:
            progress(f"⚠ Rate-limited — using {total_hits} hits gathered so far")
            break
        # Bail if many consecutive failures (network down)
        if session.consecutive_failures >= 5 and total_hits == 0:
            progress("✗ Multiple consecutive search failures — aborting this stage")
            break

        q = build_search_string(spec, lyrics, use_genre_operator=False)
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


def _target_count(
    *,
    tracks_requested: int,
    min_tracks: int | None,
    min_hours: float | None,
) -> int:
    """Soft upper aim for track count (quality may stop earlier)."""
    n = max(tracks_requested, min_tracks or 0)
    # Duration → rough track estimate (~3.5 min/track); still a soft aim only
    if min_hours and min_hours > 0:
        est = int((min_hours * 60) / 3.5) + 1
        n = max(n, est)
    return min(n, 100)


def _quality_floor(lyrics: LyricsPreference) -> float:
    """Minimum score to include when quality-first soft targets apply."""
    if lyrics.normalized().is_instrumental_only:
        return 28.0
    return 18.0


def _should_search_more(
    tracks: list[RankedTrack],
    target: int,
    min_hours: float | None,
    *,
    quality_floor: float = 0.0,
) -> bool:
    """
    Whether to keep searching for more candidates.

    Soft: stop once we have a solid quality set near ~60% of the target
    (or a reasonable duration). Never treat length as a hard requirement.
    """
    if not tracks:
        return True
    good = [t for t in tracks if t.score >= quality_floor] if quality_floor > 0 else tracks
    soft_n = max(6, int(target * 0.6)) if target else 6
    if len(good) < soft_n:
        return True
    if min_hours and min_hours > 0:
        hours = total_duration_ms(good) / 3_600_000
        # Only keep searching if substantially under the soft duration aim
        if hours < min_hours * 0.55:
            return True
    return False


def _pick_quality(
    pool: list[RankedTrack],
    target: int,
    *,
    max_per_artist: int,
    lyrics: LyricsPreference,
    book_energy: float | None = None,
    cohesive: bool = False,
) -> list[RankedTrack]:
    """
    Quality-first selection up to a soft target.

    Never pads with weak tracks just to hit a number. Applies overall-mode
    cohesion when requested. Always de-duplicates.
    """
    candidates = list(pool)
    if cohesive and book_energy is not None:
        candidates = apply_overall_cohesion(candidates, book_energy=book_energy)

    floor = _quality_floor(lyrics)
    chosen = select_diverse(
        candidates, target, max_per_artist=max_per_artist, min_score=floor
    )
    # Only lower the floor if the playlist is still very thin
    if len(chosen) < max(5, int(target * 0.35)):
        chosen = select_diverse(
            candidates,
            target,
            max_per_artist=max_per_artist,
            min_score=floor * 0.55,
        )
    return dedupe_tracks(chosen)


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

    session = start_search_session(
        budget_seconds=settings.chapterscore_spotify_collection_budget,
        hard_timeout=settings.chapterscore_spotify_timeout,
    )
    progress(
        f"Spotify search session: {session.hard_timeout:.0f}s/call, "
        f"{session.budget_seconds:.0f}s total budget"
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
    if mode == Mode.OVERALL or not analysis.chapters:
        progress("Overall mode: cohesive emotional world (shuffle-friendly)")
    else:
        progress("Chapter mode: ordered narrative progression")

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

    return dedupe_tracks(result)


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
        f"Selecting up to ~{target} tracks (soft target; quality first"
        f"; requested={tracks_requested}"
        + (f", min_hours={min_hours}" if min_hours else "")
        + ")…"
    )

    session = get_search_session()
    max_art = _max_per_artist(taste.prefs.exploration)

    def _stage_ok() -> bool:
        return not session.budget_exhausted()

    def _finalize(pool: list[RankedTrack], *, label: str) -> list[RankedTrack]:
        chosen = _pick_quality(
            pool,
            target,
            max_per_artist=max_art,
            lyrics=lyrics,
            book_energy=analysis.overall_energy,
            cohesive=True,
        )
        if chosen:
            progress(
                f"✓ {label}: {len(chosen)} tracks "
                f"(duration ≈ {total_duration_ms(chosen) / 60000:.0f} min; "
                f"soft aim was ~{target})"
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
    if lyrics.is_instrumental_only:
        progress(
            "Stage 1 — Book-vibe instrumental search "
            f"(mood={analysis.overall_mood!r}, energy={analysis.overall_energy:.2f}; "
            "strict no-vocals; cohesive overall world)"
        )
        primary_specs = vibe_instrumental_queries(analysis, max_queries=20)
        for sq in expand_queries_from_analysis(
            analysis, lyrics, max_queries=10, cohesive_overall=True
        ):
            primary_specs.append(sq)
        if personal_specs:
            # Soft personal seeds after book vibe (instrumental-flavored)
            primary_specs = primary_specs + personal_specs[:4]
        strict1 = InstrumentalStrictness.STRICT
        early1 = max(140, target * 9)
        limit_q = 30
    else:
        primary_specs = expand_queries_from_analysis(
            analysis, lyrics, max_queries=14, cohesive_overall=True
        )
        if personal_specs and taste.prefs.exploration <= 55:
            primary_specs = personal_specs[:6] + primary_specs
        elif personal_specs:
            primary_specs = personal_specs[:3] + primary_specs
        progress(f"Stage 1 — {len(primary_specs)} book-vibe + taste queries (cohesive overall)")
        strict1 = InstrumentalStrictness.PERMISSIVE
        early1 = max(100, target * 6)
        limit_q = None

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
            early_stop_raw=60,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, pool3)
        chosen = _finalize(pool, label="Stage 3")
        if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
            return chosen

    # ── Stage 4: cinematic / soundtrack fallback bank ─────────────────────
    cinema: list[SearchQuerySpec] = []
    if _stage_ok() and not session.rate_limited:
        progress(
            f"Stage 4 — cinematic fallback bank ({len(chosen)}/~{target} so far)"
        )
        cinema = cinematic_fallback_queries(analysis, lyrics, max_queries=8)
        pool4 = _search_pool(
            sp,
            cinema,
            lyrics,
            strictness=strictness3,
            progress=progress,
            label="4/cinematic",
            early_stop_raw=60,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, pool4)
        chosen = _finalize(pool, label="Stage 4")
        if chosen and not _should_search_more(chosen, target, min_hours, quality_floor=q_floor):
            return chosen

    # ── Stage 5: last resort (instrumental-only still hard-blocks vocals) ──
    progress(
        f"Stage 5 — last-resort quality pick ({len(chosen)}/~{target} so far; no weak padding)"
    )
    if lyrics.is_instrumental_only and _stage_ok() and not session.rate_limited:
        pool5 = _search_pool(
            sp,
            (cinema or cinematic_fallback_queries(analysis, lyrics, max_queries=4))[:4],
            lyrics,
            strictness=InstrumentalStrictness.PERMISSIVE,
            progress=progress,
            label="5/permissive",
            early_stop_raw=40,
            taste=taste,
            analysis=analysis,
        )
        pool = _merge_unique(pool, pool5)

    chosen = _finalize(pool, label="Stage 5")
    if chosen:
        return chosen

    # Absolute last ditch: one ultra-generic search (only if still empty)
    progress("Stage 6 — ultra-generic safety net")
    emergency_q = (
        "cinematic orchestral soundtrack"
        if lyrics.is_instrumental_only or lyrics.prefers_instrumental
        else "cinematic soundtrack"
    )
    pool6 = _search_pool(
        sp,
        [SearchQuerySpec(query=emergency_q, reason="emergency")],
        lyrics,
        strictness=InstrumentalStrictness.RELAXED
        if lyrics.is_instrumental_only
        else InstrumentalStrictness.PERMISSIVE,
        progress=progress,
        label="6/emergency",
        limit_per_query=50,
        taste=taste,
        analysis=analysis,
    )
    chosen = _finalize(pool6, label="Emergency")
    if chosen:
        progress(f"✓ Emergency net returned {len(chosen)} tracks")
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
            f"Global fill — playlist has {len(final)} tracks, soft aim ~{target} "
            "(quality first)"
        )
        unused = [t for t in global_pool if t.id not in seen_ids]
        extra = select_diverse(
            unused,
            max(0, target - len(final)),
            max_per_artist=2,
            min_score=q_floor * 0.7,
        )
        for t in extra:
            if t.id in seen_ids:
                continue
            seen_ids.add(t.id)
            final.append(t)

    if _should_search_more(final, target, min_hours, quality_floor=q_floor):
        progress("Global fill — cinematic bank (only if still thin)")
        cinema = cinematic_fallback_queries(analysis, lyrics, max_queries=12)
        pool_c = _search_pool(
            sp,
            cinema,
            lyrics,
            strictness=InstrumentalStrictness.RELAXED
            if lyrics.is_instrumental_only
            else InstrumentalStrictness.PERMISSIVE,
            seen_ids=seen_ids,
            progress=progress,
            label="global/cinematic",
            taste=taste,
            analysis=analysis,
        )
        extra = select_diverse(
            [t for t in pool_c if t.id not in seen_ids],
            max(0, target - len(final)),
            max_per_artist=max(max_art, 2),
            min_score=q_floor * 0.55,
        )
        for t in extra:
            if t.id in seen_ids:
                continue
            seen_ids.add(t.id)
            final.append(t)

    final = dedupe_tracks(final)
    progress(f"Chapter mode assembled {len(final)} tracks (soft aim ~{target})")
    return final
