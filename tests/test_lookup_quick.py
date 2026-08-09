"""Tests for lightweight Step 1 book lookup (no Grok / deep wiki)."""

from chapterscore.books.aggregator import lookup_book_quick
from chapterscore.models import BookMetadata


def test_lookup_book_quick_is_exported():
    from chapterscore.books import lookup_book_quick as exported

    assert callable(exported)
    assert exported is lookup_book_quick


def test_has_chapter_hint_helpers():
    """Mirrors web has_chapter_data_hint logic."""
    plain = BookMetadata(title="X", raw={"quick_lookup": True, "quick_chapter_hint": False})
    hinted = BookMetadata(title="Y", raw={"quick_lookup": True, "quick_chapter_hint": True})
    assert not (plain.raw or {}).get("quick_chapter_hint")
    assert (hinted.raw or {}).get("quick_chapter_hint") is True


def test_quick_chapter_list_available_handles_miss():
    from chapterscore.books.wikipedia import quick_chapter_list_available

    # Nonsense title should return False quickly without raising
    assert quick_chapter_list_available("zzzznotarealbooktitle999xyz") is False
