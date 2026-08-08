"""Tests for domain models."""

from chapterscore.models import (
    BookMetadata,
    BookVibeAnalysis,
    ChapterVibe,
    LyricsPreference,
    Mode,
    RankedTrack,
    SearchQuerySpec,
)


def test_book_display_name():
    book = BookMetadata(title="Dune", authors=["Frank Herbert"])
    assert book.display_name == "Dune by Frank Herbert"
    assert book.author_str == "Frank Herbert"


def test_book_no_author():
    book = BookMetadata(title="Unknown Tome")
    assert book.display_name == "Unknown Tome"
    assert book.author_str == "Unknown"


def test_vibe_energy_clamp():
    analysis = BookVibeAnalysis(
        book_title="X",
        overall_mood="tense",
        overall_energy=1.5,  # should clamp
    )
    assert analysis.overall_energy == 1.0

    analysis2 = BookVibeAnalysis(
        book_title="X",
        overall_mood="calm",
        overall_energy=-0.2,
    )
    assert analysis2.overall_energy == 0.0


def test_search_query_spec_bounds():
    q = SearchQuerySpec(query="dark ambient tension", energy=0.3, valence=0.2)
    assert q.query == "dark ambient tension"
    assert q.energy == 0.3


def test_ranked_track_display():
    t = RankedTrack(
        uri="spotify:track:abc",
        id="abc",
        name="Arrival",
        artists=["Hans Zimmer", "Lisa Gerrard"],
        score=82.5,
    )
    assert t.display == "Arrival — Hans Zimmer, Lisa Gerrard"
    assert t.artist_str == "Hans Zimmer, Lisa Gerrard"


def test_chapter_vibe_roundtrip():
    ch = ChapterVibe(
        chapter_number=1,
        chapter_title="The Beginning",
        mood="hopeful",
        energy_level=0.4,
        atmospheres=["hopeful", "intimate"],
        vibe_note="Quiet dawn before the storm.",
        search_queries=[
            SearchQuerySpec(query="intimate acoustic dawn", energy=0.35, valence=0.55)
        ],
    )
    data = ch.model_dump()
    restored = ChapterVibe.model_validate(data)
    assert restored.chapter_number == 1
    assert len(restored.search_queries) == 1


def test_enums():
    assert Mode.OVERALL.value == "overall"
    assert LyricsPreference.INSTRUMENTAL_ONLY.value == "instrumental-only"


def test_book_metadata_enrichment_fields():
    book = BookMetadata(
        title="1984",
        authors=["George Orwell"],
        description="A dystopian novel…",
        publisher_blurb="Winston Smith…",
        reception_text="A landmark of political fiction.",
        themes_text="Surveillance, language, power.",
        review_snippets=["bleak and devastating"],
        genre_labels=["dystopian", "literary fiction"],
    )
    assert book.publisher_blurb
    assert "Surveillance" in book.themes_text
    blob = book.analysis_context_blob()
    assert "bleak" in blob
    assert "dystopian" in blob


def test_book_vibe_multi_dimensional_defaults():
    analysis = BookVibeAnalysis(
        book_title="X",
        overall_mood="wry",
        overall_energy=0.4,
        narrative_voice="wry first-person",
        distinctive_signature="not a generic dystopia",
        intimacy_vs_epic=0.8,
    )
    assert analysis.narrative_voice.startswith("wry")
    assert analysis.intimacy_vs_epic == 0.8
    assert "wry" in " ".join(analysis.vibe_keyword_pool()).lower()
    assert analysis.style_keywords_good() == []
