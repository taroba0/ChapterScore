"""Tests for analysis prompt construction (literature-first multi-pass)."""

from chapterscore.analysis.prompts import (
    LITERARY_SYSTEM,
    MUSIC_SYSTEM,
    SYSTEM_PROMPT,
    build_literary_prompt,
    build_music_prompt,
    build_user_prompt,
)
from chapterscore.models import BookMetadata, ChapterSummary, LyricsPreference, Mode


def _sample_book() -> BookMetadata:
    return BookMetadata(
        title="The Great Gatsby",
        authors=["F. Scott Fitzgerald"],
        description="A mysterious millionaire…",
        plot_summary="Nick Carraway recounts his summer among the wealthy…",
        subjects=["Fiction", "Jazz Age"],
        publisher_blurb="A portrait of the Jazz Age…",
        reception_text="Critics praised its bittersweet irony and lyrical prose.",
        review_snippets=["wry and elegant", "bittersweet portrait of ambition"],
        genre_labels=["literary fiction"],
        chapters=[
            ChapterSummary(number=1, title="Nick arrives", summary="Nick moves to West Egg."),
            ChapterSummary(number=2, title="The valley of ashes"),
        ],
        source="test",
    )


def test_system_prompt_mentions_json():
    assert "JSON" in SYSTEM_PROMPT
    assert "JSON" in LITERARY_SYSTEM


def test_literary_system_is_anti_generic():
    assert "Anti-generic" in LITERARY_SYSTEM or "anti-generic" in LITERARY_SYSTEM.lower()
    assert "dystop" in LITERARY_SYSTEM.lower() or "genre template" in LITERARY_SYSTEM.lower()


def test_literary_prompt_requires_signature():
    book = _sample_book()
    prompt = build_literary_prompt(book, mode=Mode.OVERALL)
    assert "The Great Gatsby" in prompt
    assert "distinctive_signature" in prompt
    assert "genre_peers_contrast" in prompt
    assert "narrative_voice" in prompt
    assert "intimacy_vs_epic" in prompt
    assert "bittersweet" in prompt or "Publisher" in prompt


def test_music_prompt_literature_first():
    book = _sample_book()
    literary = {
        "book_title": "The Great Gatsby",
        "overall_mood": "bittersweet yearning",
        "distinctive_signature": "wry first-person elegy for American ambition",
        "intimacy_vs_epic": 0.75,
        "anti_generic_notes": ["NOT epic battle music"],
    }
    prompt = build_music_prompt(
        book, literary, mode=Mode.OVERALL, lyrics=LyricsPreference.INSTRUMENTAL_ONLY
    )
    assert "LITERARY PROFILE" in prompt
    assert "bittersweet yearning" in prompt
    assert "suitable_styles" in prompt
    assert "avoid_styles" in prompt
    assert "instrumental" in prompt.lower()
    assert MUSIC_SYSTEM  # module loads


def test_overall_prompt_contains_book():
    book = _sample_book()
    prompt = build_user_prompt(book, mode=Mode.OVERALL, lyrics=LyricsPreference.NO)
    assert "The Great Gatsby" in prompt
    assert "F. Scott Fitzgerald" in prompt
    assert "MODE: overall" in prompt
    assert "overall_search_queries" in prompt
    assert "distinctive_signature" in prompt


def test_chapter_prompt_lists_chapters():
    book = _sample_book()
    prompt = build_user_prompt(
        book, mode=Mode.CHAPTER, lyrics=LyricsPreference.INSTRUMENTAL_ONLY
    )
    assert "MODE: chapter" in prompt
    assert "Chapter 1" in prompt or "Ch 1" in prompt or "Nick arrives" in prompt
    assert "instrumental" in prompt.lower()


def test_lyrics_yes_instruction():
    book = _sample_book()
    prompt = build_user_prompt(book, mode=Mode.OVERALL, lyrics=LyricsPreference.YES)
    assert "WITH lyrics" in prompt or "vocals" in prompt.lower()
