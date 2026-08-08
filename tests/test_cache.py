"""Tests for disk cache."""

from pathlib import Path

from chapterscore.cache import Cache, book_cache_key
from chapterscore.models import BookMetadata


def test_book_cache_key_stable():
    k1 = book_cache_key("Dune", "Frank Herbert")
    k2 = book_cache_key("Dune", "Frank Herbert")
    k3 = book_cache_key("dune", "frank herbert")
    assert k1 == k2 == k3
    assert book_cache_key("Dune") != book_cache_key("Dune", "Herbert")


def test_cache_roundtrip(tmp_path: Path):
    cache = Cache(root=tmp_path / "entries", ttl_hours=24)
    book = BookMetadata(
        title="Neuromancer",
        authors=["William Gibson"],
        description="A console cowboy…",
        source="test",
    )
    cache.set("book", "neuromancer|william gibson", book)
    loaded = cache.get_model("book", "neuromancer|william gibson", BookMetadata)
    assert loaded is not None
    assert loaded.title == "Neuromancer"
    assert loaded.authors == ["William Gibson"]


def test_cache_miss(tmp_path: Path):
    cache = Cache(root=tmp_path / "entries", ttl_hours=24)
    assert cache.get("book", "nope") is None
    assert cache.get_model("book", "nope", BookMetadata) is None


def test_cache_expiry(tmp_path: Path):
    cache = Cache(root=tmp_path / "entries", ttl_hours=0.0000001)  # essentially immediate
    cache.set("book", "x", {"title": "Y"})
    # Force expiry by rewriting expires_at into the past via zero TTL on next get
    # With tiny TTL, a short sleep ensures expiry
    import time

    time.sleep(0.05)
    # Recreate with same tiny TTL so get() treats as expired
    cache2 = Cache(root=tmp_path / "entries", ttl_hours=0.0000001)
    # The stored _expires_at was set at write time with tiny TTL — should be expired
    assert cache2.get("book", "x") is None


def test_cache_delete(tmp_path: Path):
    cache = Cache(root=tmp_path / "entries", ttl_hours=24)
    cache.set("vibe", "key1", {"overall_mood": "dark"})
    assert cache.get("vibe", "key1") is not None
    cache.delete("vibe", "key1")
    assert cache.get("vibe", "key1") is None
