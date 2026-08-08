"""Tests for analysis JSON parsing and lyrics enforcement."""

import pytest

from chapterscore.analysis.grok import _enforce_lyrics_preference, _extract_json
from chapterscore.exceptions import AnalysisError
from chapterscore.models import (
    BookVibeAnalysis,
    LyricsPreference,
    SearchQuerySpec,
)


def test_extract_json_plain():
    data = _extract_json('{"book_title": "Dune", "overall_mood": "epic", "overall_energy": 0.7}')
    assert data["book_title"] == "Dune"


def test_extract_json_fenced():
    text = """Here you go:
```json
{"book_title": "Dune", "overall_mood": "epic", "overall_energy": 0.7}
```
"""
    data = _extract_json(text)
    assert data["overall_mood"] == "epic"


def test_extract_json_trailing_comma():
    text = '{"book_title": "X", "overall_mood": "calm", "overall_energy": 0.3,}'
    data = _extract_json(text)
    assert data["book_title"] == "X"


def test_extract_json_invalid():
    with pytest.raises(AnalysisError):
        _extract_json("not json at all")


def test_enforce_instrumental_only():
    analysis = BookVibeAnalysis(
        book_title="X",
        overall_mood="dark",
        overall_energy=0.5,
        overall_search_queries=[
            SearchQuerySpec(query="tense thriller cues", energy=0.6),
            SearchQuerySpec(query="ambient score", energy=0.3, instrumentalness_min=0.5),
        ],
    )
    fixed = _enforce_lyrics_preference(analysis, LyricsPreference.INSTRUMENTAL_ONLY)
    for q in fixed.overall_search_queries:
        assert q.instrumentalness_min is not None
        assert q.instrumentalness_min >= 0.75
    assert "instrumental" in fixed.overall_search_queries[0].query.lower()
    # Second already has score keyword — may or may not append
    assert fixed.overall_search_queries[1].instrumentalness_min >= 0.75


def test_enforce_lyrics_yes_clears_high_instrumentalness():
    analysis = BookVibeAnalysis(
        book_title="X",
        overall_mood="romantic",
        overall_energy=0.4,
        overall_search_queries=[
            SearchQuerySpec(query="romantic ballad", instrumentalness_min=0.9),
        ],
    )
    fixed = _enforce_lyrics_preference(analysis, LyricsPreference.YES)
    assert fixed.overall_search_queries[0].instrumentalness_min is None
