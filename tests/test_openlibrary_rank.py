"""Tests for Open Library result ranking."""

from chapterscore.books.openlibrary import _normalize_title, _pick_best_doc


def test_normalize_title_strips_article_and_subtitle():
    assert _normalize_title("The Great Gatsby") == "great gatsby"
    assert _normalize_title("Dune: Deluxe Edition") == "dune"


def test_pick_exact_title_over_sequel():
    docs = [
        {
            "title": "Children of Dune",
            "author_name": ["Frank Herbert"],
            "edition_count": 120,
            "first_publish_year": 1976,
        },
        {
            "title": "Dune",
            "author_name": ["Frank Herbert"],
            "edition_count": 200,
            "first_publish_year": 1965,
        },
        {
            "title": "Dune Messiah",
            "author_name": ["Frank Herbert"],
            "edition_count": 90,
            "first_publish_year": 1969,
        },
    ]
    best = _pick_best_doc(docs, "Dune", "Frank Herbert")
    assert best["title"] == "Dune"


def test_pick_prefers_author_match():
    docs = [
        {
            "title": "Dune",
            "author_name": ["Someone Else"],
            "edition_count": 500,
            "first_publish_year": 2000,
        },
        {
            "title": "Dune",
            "author_name": ["Frank Herbert"],
            "edition_count": 100,
            "first_publish_year": 1965,
        },
    ]
    best = _pick_best_doc(docs, "Dune", "Frank Herbert")
    assert best["author_name"] == ["Frank Herbert"]
