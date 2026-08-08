"""xAI Grok-powered literary vibe analysis (literature-first, multi-pass)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from chapterscore.analysis.prompts import (
    LITERARY_SYSTEM,
    MUSIC_SYSTEM,
    SYSTEM_PROMPT,
    build_literary_prompt,
    build_music_prompt,
    build_user_prompt,
)
from chapterscore.cache import Cache, book_cache_key
from chapterscore.config import get_settings
from chapterscore.exceptions import AnalysisError, ConfigError
from chapterscore.models import (
    BookMetadata,
    BookVibeAnalysis,
    ChapterVibe,
    EmotionalAct,
    LyricsPreference,
    Mode,
    SearchQuerySpec,
)

logger = logging.getLogger(__name__)

# Bump when analysis quality/schema changes so old generic caches invalidate
_ANALYSIS_CACHE_VERSION = "litv2"


def _client() -> OpenAI:
    settings = get_settings()
    if not settings.xai_api_key:
        raise ConfigError(
            "XAI_API_KEY is not set.",
            hint="Copy .env.example to .env and add your key from https://console.x.ai",
        )
    return OpenAI(api_key=settings.xai_api_key, base_url=settings.xai_base_url)


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from a model response, tolerating accidental fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AnalysisError(
            "Model did not return JSON.",
            hint="Try again; the model response was malformed.",
        )
    blob = text[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        repaired = re.sub(r",\s*}", "}", blob)
        repaired = re.sub(r",\s*]", "]", repaired)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise AnalysisError(
                f"Failed to parse analysis JSON: {exc}",
                hint="Re-run the command; cached book data will be reused.",
            ) from exc


def _enforce_lyrics_preference(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
) -> BookVibeAnalysis:
    """Hard-enforce instrumental constraints on query specs."""
    mode = lyrics.normalized()

    def fix_queries(queries: list[SearchQuerySpec]) -> list[SearchQuerySpec]:
        fixed: list[SearchQuerySpec] = []
        for q in queries:
            data = q.model_dump()
            if mode is LyricsPreference.INSTRUMENTAL_ONLY:
                data["instrumentalness_min"] = max(
                    data.get("instrumentalness_min") or 0.0, 0.75
                )
                ql = q.query.lower()
                if not any(
                    k in ql
                    for k in (
                        "instrumental",
                        "soundtrack",
                        "score",
                        "ambient",
                        "orchestral",
                        "classical",
                        "piano",
                        "no vocals",
                    )
                ):
                    data["query"] = f"{q.query} instrumental"
            elif mode is LyricsPreference.PREFER_INSTRUMENTAL:
                data["instrumentalness_min"] = max(
                    data.get("instrumentalness_min") or 0.0, 0.45
                )
            else:
                if data.get("instrumentalness_min") and data["instrumentalness_min"] > 0.5:
                    data["instrumentalness_min"] = None
            fixed.append(SearchQuerySpec.model_validate(data))
        return fixed

    analysis.overall_search_queries = fix_queries(analysis.overall_search_queries)
    for ch in analysis.chapters:
        ch.search_queries = fix_queries(ch.search_queries)
    for act in analysis.emotional_acts:
        act.search_queries = fix_queries(act.search_queries)

    if not analysis.suitable_styles and analysis.suggested_genres:
        analysis.suitable_styles = list(analysis.suggested_genres)[:8]

    # Honor anti-generic notes into avoid_styles when missing
    if analysis.anti_generic_notes and not analysis.avoid_styles:
        analysis.avoid_styles = [
            n.replace("NOT ", "").replace("not ", "").strip()[:60]
            for n in analysis.anti_generic_notes[:8]
            if n
        ]

    return analysis


def _merge_literary_into_music(
    literary: dict[str, Any], music: dict[str, Any]
) -> dict[str, Any]:
    """Prefer music-pass fields when present; keep literary depth as backbone."""
    merged = dict(literary)
    for k, v in music.items():
        if v is None or v == "" or v == []:
            continue
        merged[k] = v
    # Always keep distinctive literary fields if music pass dropped them
    for key in (
        "distinctive_signature",
        "genre_peers_contrast",
        "anti_generic_notes",
        "narrative_voice",
        "writing_style",
        "setting_texture",
        "sensory_atmosphere",
        "dominant_tones",
        "secondary_tones",
        "pacing_profile",
    ):
        if not merged.get(key) and literary.get(key):
            merged[key] = literary[key]
    return merged


def _acts_to_chapters_if_needed(
    analysis: BookVibeAnalysis, *, mode: Mode, book: BookMetadata
) -> BookVibeAnalysis:
    """
    Chapter mode with synthetic/weak chapters → use emotional_acts as chapter arcs.
    """
    if mode != Mode.CHAPTER:
        return analysis
    synthetic = bool(book.raw.get("synthetic_chapters"))
    weak_chapters = not analysis.chapters or synthetic
    if weak_chapters and analysis.emotional_acts:
        chapters: list[ChapterVibe] = []
        for act in analysis.emotional_acts:
            chapters.append(
                ChapterVibe(
                    chapter_number=act.act_id,
                    chapter_title=act.label or f"Act {act.act_id}",
                    mood=act.mood or analysis.overall_mood,
                    energy_level=act.energy_level,
                    atmospheres=act.atmospheres or list(analysis.atmospheres[:4]),
                    emotional_arc=act.emotional_arc,
                    pacing=act.pacing or analysis.pacing,
                    tone=act.tone or analysis.tone,
                    vibe_note=act.vibe_note,
                    search_queries=list(act.search_queries),
                )
            )
        analysis.chapters = chapters
    return analysis


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=6), reraise=True)
def _call_grok(system: str, user: str, *, temperature: float = 0.35) -> str:
    settings = get_settings()
    client = _client()
    try:
        response = client.chat.completions.create(
            model=settings.xai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=8192,
        )
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise AnalysisError("Empty response from Grok.")
        return content
    except AnalysisError:
        raise
    except Exception as exc:
        logger.debug("chat.completions failed (%s); trying responses API", exc)
        try:
            response = client.responses.create(
                model=settings.xai_model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            text = getattr(response, "output_text", None)
            if not text:
                parts: list[str] = []
                for item in getattr(response, "output", []) or []:
                    for block in getattr(item, "content", []) or []:
                        t = getattr(block, "text", None)
                        if t:
                            parts.append(t)
                text = "\n".join(parts)
            if not text or not str(text).strip():
                raise AnalysisError(f"Grok API error: {exc}") from exc
            return str(text)
        except AnalysisError:
            raise
        except Exception as exc2:
            raise AnalysisError(
                f"Grok API request failed: {exc2}",
                hint="Verify XAI_API_KEY and that your xAI account has credits.",
            ) from exc2


def analyze_book_vibe(
    book: BookMetadata,
    *,
    mode: Mode = Mode.OVERALL,
    lyrics: LyricsPreference = LyricsPreference.ALLOW_LYRICS,
    use_cache: bool = True,
) -> BookVibeAnalysis:
    """
    Literature-first multi-pass analysis (cached).

    Pass 1 — pure literary profile (voice, tone, setting, distinctive signature).
    Pass 2 — map that profile into Spotify-ready queries and style lists.
    Falls back to a single combined prompt if pass 2 fails.
    """
    settings = get_settings()
    if not settings.xai_api_key:
        raise ConfigError(
            "XAI_API_KEY is not set.",
            hint="Copy .env.example to .env and add your key from https://console.x.ai",
        )

    lyrics = lyrics.normalized()
    cache = Cache()
    cache_key = (
        book_cache_key(book.title, book.authors[0] if book.authors else None, book.isbn)
        + f"|{mode.value}|{lyrics.value}|{settings.xai_model}|{_ANALYSIS_CACHE_VERSION}"
    )

    if use_cache:
        cached = cache.get_model("vibe", cache_key, BookVibeAnalysis)
        if cached is not None:
            # Prefer cache hits that already have distinctive literary fields
            if cached.distinctive_signature or cached.narrative_voice:
                logger.debug("Vibe cache hit")
                return _enforce_lyrics_preference(cached, lyrics)

    # ── Pass 1: literature ────────────────────────────────────────────────
    literary_raw = _call_grok(
        LITERARY_SYSTEM,
        build_literary_prompt(book, mode=mode),
        temperature=0.3,
    )
    literary = _extract_json(literary_raw)

    # ── Pass 2: music mapping ─────────────────────────────────────────────
    try:
        music_raw = _call_grok(
            MUSIC_SYSTEM,
            build_music_prompt(book, literary, mode=mode, lyrics=lyrics),
            temperature=0.35,
        )
        music = _extract_json(music_raw)
        data = _merge_literary_into_music(literary, music)
    except Exception as exc:
        logger.warning("Music pass failed (%s); falling back to single-pass", exc)
        combined = build_user_prompt(book, mode=mode, lyrics=lyrics)
        raw = _call_grok(SYSTEM_PROMPT, combined, temperature=0.35)
        data = _extract_json(raw)
        # Overlay literary fields if combined was thin
        data = _merge_literary_into_music(literary, data)

    try:
        analysis = BookVibeAnalysis.model_validate(data)
    except Exception as exc:
        # Soft repair: inject required overall_mood from literary
        if not data.get("overall_mood") and literary.get("overall_mood"):
            data["overall_mood"] = literary["overall_mood"]
        if data.get("overall_energy") is None:
            data["overall_energy"] = literary.get("overall_energy", 0.5)
        try:
            analysis = BookVibeAnalysis.model_validate(data)
        except Exception as exc2:
            raise AnalysisError(
                f"Analysis JSON did not match expected schema: {exc2}",
                hint="Re-run with --no-cache; if it persists, try overall mode first.",
            ) from exc2

    if not analysis.book_title:
        analysis.book_title = book.title
    if not analysis.authors:
        analysis.authors = book.authors
    if not analysis.playlist_title_suggestion:
        analysis.playlist_title_suggestion = f"ChapterScore: {book.title}"
    if not analysis.distinctive_signature and literary.get("distinctive_signature"):
        analysis.distinctive_signature = literary["distinctive_signature"]

    analysis = _acts_to_chapters_if_needed(analysis, mode=mode, book=book)
    analysis = _enforce_lyrics_preference(analysis, lyrics)

    if use_cache:
        cache.set("vibe", cache_key, analysis)

    return analysis
