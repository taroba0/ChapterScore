"""Stage 1 multi-strategy discovery (no Grok)."""

from chapterscore.books.discovery import (
    normalize_title_key,
    score_candidate,
    search_book_candidates,
    title_query_variants,
    _dedupe_and_rank,
    _title_similarity,
)
from chapterscore.models import BookMetadata


def test_title_variants_include_simplified():
    variants = title_query_variants("I Who Have Never Known Men")
    assert variants[0] == "I Who Have Never Known Men"
    assert any("never known men" in v.lower() for v in variants)
    # no empty / pure-dupe explosion
    assert 2 <= len(variants) <= 6


def test_title_variants_strip_subtitle():
    variants = title_query_variants("Dune: Deluxe Edition")
    assert any(normalize_title_key(v) == "dune" for v in variants)


def test_title_similarity_exact_and_partial():
    assert _title_similarity("I Who Have Never Known Men", "I Who Have Never Known Men") == 1.0
    assert _title_similarity(
        "I Who Have Never Known Men", "I Who Have Never Known Men: A Novel"
    ) >= 0.85
    assert _title_similarity("Dune", "The Hunger Games") < 0.4


def test_rank_prefers_title_and_author_match():
    query = "I Who Have Never Known Men"
    author = "Jacqueline Harpman"
    good = BookMetadata(
        title="I Who Have Never Known Men",
        authors=["Jacqueline Harpman"],
        publish_year=1995,
        page_count=208,
        source="googlebooks",
    )
    wrong = BookMetadata(
        title="Men Who Hate Women",
        authors=["Someone Else"],
        publish_year=2005,
        source="openlibrary",
    )
    ranked = _dedupe_and_rank(
        [wrong, good],
        query_title=query,
        query_author=author,
        limit=5,
    )
    assert ranked[0].title.startswith("I Who Have Never Known Men")
    assert score_candidate(good, query_title=query, query_author=author) > score_candidate(
        wrong, query_title=query, query_author=author
    )


def test_search_finds_harpman_title():
    """Live network smoke — multi-strategy should surface this popular title."""
    cands = search_book_candidates(
        "I Who Have Never Known Men",
        author="Jacqueline Harpman",
        limit=8,
        use_cache=False,
    )
    assert cands
    blob = " ".join(f"{c.title} {' '.join(c.authors)}" for c in cands).lower()
    assert "never known men" in blob or "harpman" in blob
    # Prefer English title match among candidates when present
    englishish = [
        c
        for c in cands
        if _title_similarity("I Who Have Never Known Men", c.title) >= 0.5
    ]
    assert englishish, f"no English title among: {[c.title for c in cands]}"
    # Top result should be a strong English match when available
    top = cands[0]
    assert _title_similarity("I Who Have Never Known Men", top.title) >= 0.5, top.title
