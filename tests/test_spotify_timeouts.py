"""Tests for non-hanging Spotify search: timeouts, 429, skip-on-fail."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from spotipy.exceptions import SpotifyException

from chapterscore.spotify import search as search_mod
from chapterscore.spotify.search import (
    SearchSession,
    SpotifyRateLimitError,
    SpotifyTimeoutError,
    _call_with_timeout,
    _spotify_call,
    end_search_session,
    search_tracks_resilient,
    start_search_session,
)


def setup_function():
    end_search_session()
    start_search_session(budget_seconds=30, hard_timeout=2.0)


def teardown_function():
    end_search_session()


def test_call_with_timeout_raises_on_slow_fn():
    def slow():
        time.sleep(5)
        return "done"

    with pytest.raises(SpotifyTimeoutError):
        _call_with_timeout(slow, timeout=0.3, label="slow-test")


def test_call_with_timeout_returns_fast_result():
    assert _call_with_timeout(lambda: 42, timeout=2.0, label="fast") == 42


def test_spotify_call_429_then_skip():
    session = SearchSession(hard_timeout=2.0, max_429_sleep=0.05, budget_seconds=10)

    def boom():
        raise SpotifyException(429, -1, "Too many requests", headers={"Retry-After": "1"})

    with pytest.raises(SpotifyRateLimitError):
        _spotify_call(boom, label="rate", retries=0, session=session)

    assert session.rate_limited is True


def test_spotify_call_429_retries_once_then_ok():
    session = SearchSession(hard_timeout=2.0, max_429_sleep=0.05, budget_seconds=10)
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] == 1:
            raise SpotifyException(429, -1, "rate", headers={"Retry-After": "0"})
        return {"ok": True}

    result = _spotify_call(flaky, label="flaky", retries=1, session=session)
    assert result == {"ok": True}
    assert state["n"] == 2


def test_resilient_search_skips_timeout_and_returns_empty():
    session = start_search_session(budget_seconds=20, hard_timeout=0.4)
    sp = MagicMock()

    def hang(**_kwargs):
        time.sleep(3)
        return {"tracks": {"items": []}}

    sp.search.side_effect = hang
    logs: list[str] = []
    results = search_tracks_resilient(
        sp, "1920s jazz noir orchestral", session=session, progress=logs.append
    )
    assert results == []
    assert session.queries_timed_out >= 1
    assert any("Timeout" in m or "⏱" in m for m in logs)


def test_resilient_search_continues_after_api_error():
    session = start_search_session(budget_seconds=20, hard_timeout=2.0)
    sp = MagicMock()
    sp.search.side_effect = SpotifyException(500, -1, "boom", headers={})
    logs: list[str] = []
    results = search_tracks_resilient(
        sp, "epic orchestral", session=session, progress=logs.append
    )
    assert results == []
    assert session.queries_failed >= 1
    assert any("failed" in m.lower() or "✗" in m for m in logs)


def test_resilient_search_returns_hits():
    session = start_search_session(budget_seconds=20, hard_timeout=2.0)
    sp = MagicMock()
    sp.search.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "abc",
                    "name": "Theme",
                    "artists": [{"name": "Zimmer"}],
                    "uri": "spotify:track:abc",
                    "popularity": 50,
                    "duration_ms": 200000,
                    "explicit": False,
                    "album": {"name": "Score"},
                    "external_urls": {},
                }
            ]
        }
    }
    results = search_tracks_resilient(sp, "hans zimmer", session=session)
    assert len(results) == 1
    assert results[0]["id"] == "abc"
    assert session.queries_ok == 1


def test_budget_exhausted_short_circuits():
    session = start_search_session(budget_seconds=0.01, hard_timeout=2.0)
    time.sleep(0.05)
    sp = MagicMock()
    logs: list[str] = []
    results = search_tracks_resilient(sp, "anything", session=session, progress=logs.append)
    assert results == []
    assert sp.search.call_count == 0
    assert any("budget" in m.lower() or "⏱" in m for m in logs)


def test_search_string_variants_capped():
    from chapterscore.spotify.search import search_string_variants

    v = search_string_variants("one two three four five six")
    assert len(v) <= 2
    assert v[0].startswith("one")


def test_search_string_variants_prefer_short():
    from chapterscore.spotify.search import search_string_variants

    v = search_string_variants(
        "locker-room bravado piano soft instrumental", prefer_short=True
    )
    assert len(v) == 2
    assert len(v[0].split()) <= len(v[1].split())
    assert "instrumental" in v[0].lower()


def test_resilient_tries_short_after_long_timeout():
    """Long phrase times out once; shorter variant is still attempted."""
    session = start_search_session(budget_seconds=30, hard_timeout=0.35)
    sp = MagicMock()
    calls: list[str] = []

    def hang_or_hit(**kwargs):
        q = kwargs.get("q") or ""
        calls.append(q)
        # Full query includes "soft"; shortened form drops it
        if "soft" in q.lower():
            time.sleep(2)
            return {"tracks": {"items": []}}
        return {
            "tracks": {
                "items": [
                    {
                        "id": "short1",
                        "name": "Piano Theme",
                        "artists": [{"name": "Anon"}],
                        "uri": "spotify:track:short1",
                        "popularity": 40,
                        "duration_ms": 180000,
                        "explicit": False,
                        "album": {"name": "A"},
                        "external_urls": {},
                    }
                ]
            }
        }

    sp.search.side_effect = hang_or_hit
    logs: list[str] = []
    results = search_tracks_resilient(
        sp,
        "locker-room bravado piano soft instrumental",
        session=session,
        progress=logs.append,
    )
    assert len(results) == 1
    assert results[0]["id"] == "short1"
    assert session.queries_timed_out >= 1
    assert any("soft" not in c.lower() for c in calls)


def test_consecutive_timeouts_mark_search_unreliable_in_pool():
    """Stage search aborts after consecutive timeouts with 0 hits and marks session."""
    from chapterscore.models import LyricsPreference, SearchQuerySpec
    from chapterscore.spotify.ranking import InstrumentalStrictness
    from chapterscore.spotify.selection import _search_pool

    session = start_search_session(budget_seconds=60, hard_timeout=0.3)
    sp = MagicMock()

    def hang(**_kwargs):
        time.sleep(2)
        return {"tracks": {"items": []}}

    sp.search.side_effect = hang
    specs = [
        SearchQuerySpec(query=f"long vibe phrase number {i} instrumental", reason="t")
        for i in range(10)
    ]
    logs: list[str] = []
    pool = _search_pool(
        sp,
        specs,
        LyricsPreference.INSTRUMENTAL_ONLY,
        strictness=InstrumentalStrictness.STRICT,
        progress=logs.append,
        label="test",
        early_stop_raw=200,
        limit_per_query=10,
    )
    assert pool == []
    assert session.search_unreliable is True
    assert session.queries_timed_out >= 3
    # Must not have attempted all 10 long queries
    assert sp.search.call_count < 10
    assert any("fail-fast" in m.lower() or "timeout" in m.lower() for m in logs)


def test_search_unreliable_short_circuits_resilient():
    session = start_search_session(budget_seconds=20, hard_timeout=2.0)
    session.search_unreliable = True
    sp = MagicMock()
    logs: list[str] = []
    assert search_tracks_resilient(sp, "anything", session=session, progress=logs.append) == []
    assert sp.search.call_count == 0
    assert any("unreliable" in m.lower() for m in logs)
