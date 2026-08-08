"""Tests for analysis prompt construction."""

from chapterscore.analysis.prompts import SYSTEM_PROMPT, build_user_prompt
from chapterscore.models import BookMetadata, ChapterSummary, LyricsPreference, Mode


def _sample_book() -> BookMetadata:
    return BookMetadata(
        title="The Great Gatsby",
        authors=["F. Scott Fitzgerald"],
        description="A mysterious millionaire…",
        plot_summary="Nick Carraway recounts his summer among the wealthy…",
        subjects=["Fiction", "Jazz Age"],
        chapters=[
            ChapterSummary(number=1, title="Nick arrives", summary="Nick moves to West Egg."),
            ChapterSummary(number=2, title="The valley of ashes"),
        ],
        source="test",
    )


def test_system_prompt_mentions_json():
    assert "JSON" in SYSTEM_PROMPT


def test_overall_prompt_contains_book():
    book = _sample_book()
    prompt = build_user_prompt(book, mode=Mode.OVERALL, lyrics=LyricsPreference.NO)
    assert "The Great Gatsby" in prompt
    assert "F. Scott Fitzgerald" in prompt
    assert "MODE: overall" in prompt
    assert "overall_search_queries" in prompt


def test_chapter_prompt_lists_chapters():
    book = _sample_book()
    prompt = build_user_prompt(
        book, mode=Mode.CHAPTER, lyrics=LyricsPreference.INSTRUMENTAL_ONLY
    )
    assert "MODE: chapter" in prompt
    assert "Chapter 1" in prompt
    assert "instrumental" in prompt.lower()
    assert "INSTRUMENTAL" in prompt or "instrumental-only" in prompt


def test_lyrics_yes_instruction():
    book = _sample_book()
    prompt = build_user_prompt(book, mode=Mode.OVERALL, lyrics=LyricsPreference.YES)
    assert "WITH lyrics" in prompt or "vocals" in prompt.lower()
