"""Tests for query expansion and cinematic fallbacks."""

from chapterscore.models import BookVibeAnalysis, LyricsPreference, SearchQuerySpec
from chapterscore.spotify.queries import (
    broaden_specs,
    cinematic_fallback_queries,
    expand_queries_from_analysis,
)
from chapterscore.spotify.search import build_search_string, search_string_variants


def _analysis(**kwargs) -> BookVibeAnalysis:
    base = dict(
        book_title="Dune",
        authors=["Frank Herbert"],
        overall_mood="epic",
        overall_energy=0.7,
        atmospheres=["epic", "mysterious", "tense", "desert"],
        era_feel="space opera",
        key_themes=["power", "ecology", "destiny"],
        suggested_genres=["orchestral", "ambient", "hybrid trailer"],
        overall_search_queries=[
            SearchQuerySpec(query="desert empire intrigue", energy=0.6, reason="llm"),
            SearchQuerySpec(query="sandstorm tension underscore", energy=0.75, reason="llm"),
        ],
    )
    base.update(kwargs)
    return BookVibeAnalysis(**base)


def test_expand_produces_many_diverse_queries():
    specs = expand_queries_from_analysis(
        _analysis(), LyricsPreference.INSTRUMENTAL_ONLY, max_queries=24
    )
    assert len(specs) >= 12
    texts = [s.query.lower() for s in specs]
    # Should include LLM query (flavored) and cinematic/instrumental language
    assert any("desert" in t or "sandstorm" in t for t in texts)
    assert any("instrumental" in t or "soundtrack" in t or "score" in t for t in texts)
    # Sci-fi extras for Dune
    assert any("space" in t or "sci-fi" in t or "sci fi" in t or "dune" in t for t in texts)


def test_expand_instrumental_flavors_plain_queries():
    specs = expand_queries_from_analysis(
        _analysis(
            overall_search_queries=[SearchQuerySpec(query="tense thriller cues", energy=0.7)]
        ),
        LyricsPreference.INSTRUMENTAL_ONLY,
        max_queries=10,
    )
    llm = [s for s in specs if "tense thriller" in s.query.lower()]
    assert llm
    assert "instrumental" in llm[0].query.lower() or "soundtrack" in llm[0].query.lower()


def test_cinematic_fallback_instrumental():
    specs = cinematic_fallback_queries(_analysis(), LyricsPreference.INSTRUMENTAL_ONLY)
    assert len(specs) >= 8
    assert all(
        any(k in s.query.lower() for k in ("score", "soundtrack", "orchestral", "cinematic", "instrumental", "ambient", "piano"))
        for s in specs
    )


def test_broaden_shortens_queries():
    original = [
        SearchQuerySpec(
            query="dark ambient desert empire intrigue instrumental",
            energy=0.5,
            reason="test",
        )
    ]
    broad = broaden_specs(original, LyricsPreference.INSTRUMENTAL_ONLY)
    assert broad
    assert any(len(s.query.split()) <= 5 for s in broad)


def test_build_search_string_skips_genre_by_default():
    spec = SearchQuerySpec(query="melancholic piano", genres=["neoclassical"])
    q = build_search_string(spec, LyricsPreference.NO)
    assert "genre:" not in q
    assert "melancholic piano" in q


def test_build_search_string_can_use_genre():
    spec = SearchQuerySpec(query="melancholic piano", genres=["neoclassical"])
    q = build_search_string(spec, LyricsPreference.NO, use_genre_operator=True)
    assert "genre:neoclassical" in q


def test_search_variants_simplify():
    variants = search_string_variants("dark ambient desert empire intrigue instrumental")
    assert variants[0].startswith("dark")
    assert any(len(v.split()) <= 4 for v in variants)
