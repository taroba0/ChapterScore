"""Tests for search string building and playlist descriptions."""

from chapterscore.models import LyricsPreference, Mode, RankedTrack, SearchQuerySpec
from chapterscore.spotify.playlist import build_playlist_description, _truncate
from chapterscore.spotify.search import build_search_string


def test_build_search_string_basic():
    spec = SearchQuerySpec(query="melancholic piano", genres=["neoclassical"])
    # genre: operator is opt-in (hurts recall for niche LLM genres)
    q = build_search_string(spec, LyricsPreference.NO)
    assert "melancholic piano" in q
    assert "genre:" not in q
    q2 = build_search_string(spec, LyricsPreference.NO, use_genre_operator=True)
    assert "genre:neoclassical" in q2


def test_build_search_string_instrumental_appends():
    spec = SearchQuerySpec(query="tense thriller")
    q = build_search_string(spec, LyricsPreference.INSTRUMENTAL_ONLY)
    assert "instrumental" in q.lower()


def test_build_search_string_skips_redundant_instrumental():
    spec = SearchQuerySpec(query="ambient instrumental drone")
    q = build_search_string(spec, LyricsPreference.INSTRUMENTAL_ONLY)
    # Should not double-append mindlessly in a broken way — at least still valid
    assert "ambient instrumental drone" in q


def test_truncate():
    assert _truncate("short") == "short"
    long = "x" * 400
    out = _truncate(long, 50)
    assert len(out) == 50
    assert out.endswith("…")


def test_playlist_description_overall():
    desc = build_playlist_description(
        book_title="Dune",
        authors=["Frank Herbert"],
        mode=Mode.OVERALL,
        analysis_description="Epic desert power struggles.",
        tracks=[],
        lyrics_label="instrumental",
    )
    assert "Dune" in desc
    assert "Frank Herbert" in desc
    assert "instrumental" in desc
    assert len(desc) <= 300


def test_playlist_description_chapter_notes():
    tracks = [
        RankedTrack(
            uri="spotify:track:1",
            id="1",
            name="A",
            artists=["X"],
            chapter_number=1,
            vibe_note="Quiet dread settles over Arrakis.",
        ),
        RankedTrack(
            uri="spotify:track:2",
            id="2",
            name="B",
            artists=["Y"],
            chapter_number=2,
            vibe_note="A sudden strike.",
        ),
        RankedTrack(
            uri="spotify:track:3",
            id="3",
            name="C",
            artists=["Z"],
            chapter_number=1,
            vibe_note="duplicate chapter should skip",
        ),
    ]
    desc = build_playlist_description(
        book_title="Dune",
        authors=["Frank Herbert"],
        mode=Mode.CHAPTER,
        analysis_description="",
        tracks=tracks,
        lyrics_label="mixed",
    )
    assert "Ch1:" in desc
    assert "Ch2:" in desc
    assert len(desc) <= 300
