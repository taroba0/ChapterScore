"""xAI Grok-powered literary vibe analysis."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from chapterscore.analysis.prompts import SYSTEM_PROMPT, build_user_prompt
from chapterscore.cache import Cache, book_cache_key
from chapterscore.config import get_settings
from chapterscore.exceptions import AnalysisError, ConfigError
from chapterscore.models import (
    BookMetadata,
    BookVibeAnalysis,
    LyricsPreference,
    Mode,
    SearchQuerySpec,
)

logger = logging.getLogger(__name__)


def _client() -> OpenAI:
    settings = get_settings()
    if not settings.xai_api_key:
        raise ConfigError(
            "XAI_API_KEY is not set.",
            hint="Get a key at https://console.x.ai and add it to your .env file.",
        )
    return OpenAI(api_key=settings.xai_api_key, base_url=settings.xai_base_url)


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from a model response, tolerating accidental fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Find outermost object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AnalysisError("Model did not return JSON.", hint="Try again; the model response was malformed.")
    blob = text[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        # Light repair: trailing commas
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
                data["instrumentalness_min"] = max(data.get("instrumentalness_min") or 0.0, 0.75)
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
            else:  # ALLOW_LYRICS
                if data.get("instrumentalness_min") and data["instrumentalness_min"] > 0.5:
                    data["instrumentalness_min"] = None
            fixed.append(SearchQuerySpec.model_validate(data))
        return fixed

    analysis.overall_search_queries = fix_queries(analysis.overall_search_queries)
    for ch in analysis.chapters:
        ch.search_queries = fix_queries(ch.search_queries)
    # Ensure style lists exist for ranking even if model omitted them
    if not analysis.suitable_styles and analysis.suggested_genres:
        analysis.suitable_styles = list(analysis.suggested_genres)[:8]
    return analysis


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=6), reraise=True)
def _call_grok(system: str, user: str) -> str:
    settings = get_settings()
    client = _client()
    # Prefer chat.completions for broad compatibility with OpenAI-compatible APIs
    try:
        response = client.chat.completions.create(
            model=settings.xai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=8192,
        )
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise AnalysisError("Empty response from Grok.")
        return content
    except AnalysisError:
        raise
    except Exception as exc:
        # Fallback to responses API if chat is unavailable
        logger.debug("chat.completions failed (%s); trying responses API", exc)
        try:
            response = client.responses.create(
                model=settings.xai_model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.4,
            )
            text = getattr(response, "output_text", None)
            if not text:
                # Assemble from output items
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
    """Run Grok analysis (cached) and return structured vibe data."""
    settings = get_settings()
    if not settings.xai_api_key:
        raise ConfigError(
            "XAI_API_KEY is not set.",
            hint="Copy .env.example to .env and add your key from https://console.x.ai",
        )

    cache = Cache()
    cache_key = (
        book_cache_key(book.title, book.authors[0] if book.authors else None, book.isbn)
        + f"|{mode.value}|{lyrics.value}|{settings.xai_model}"
    )

    if use_cache:
        cached = cache.get_model("vibe", cache_key, BookVibeAnalysis)
        if cached is not None:
            logger.debug("Vibe cache hit")
            return _enforce_lyrics_preference(cached, lyrics)

    user_prompt = build_user_prompt(book, mode=mode, lyrics=lyrics)
    raw = _call_grok(SYSTEM_PROMPT, user_prompt)
    data = _extract_json(raw)

    try:
        analysis = BookVibeAnalysis.model_validate(data)
    except Exception as exc:
        raise AnalysisError(
            f"Analysis JSON did not match expected schema: {exc}",
            hint="Re-run; if it persists, try overall mode first.",
        ) from exc

    # Fill identity fields if model omitted them
    if not analysis.book_title:
        analysis.book_title = book.title
    if not analysis.authors:
        analysis.authors = book.authors
    if not analysis.playlist_title_suggestion:
        analysis.playlist_title_suggestion = f"ChapterScore: {book.title}"

    analysis = _enforce_lyrics_preference(analysis, lyrics)

    if use_cache:
        cache.set("vibe", cache_key, analysis)

    return analysis
