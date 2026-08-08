"""Tests for OAuth scope helpers and 403 playlist messaging."""

from chapterscore.spotify.auth import (
    REQUIRED_SCOPES,
    has_playlist_permission,
    missing_scopes,
    parse_scopes,
    required_scope_string,
)
from chapterscore.spotify.playlist import _PLAYLIST_403_HINT, _is_forbidden
from spotipy.exceptions import SpotifyException


def test_required_scopes_include_playlist_and_email():
    s = required_scope_string()
    for scope in (
        "playlist-modify-public",
        "playlist-modify-private",
        "user-read-private",
        "user-read-email",
        "user-top-read",
    ):
        assert scope in s.split()


def test_required_scopes_constant():
    assert "playlist-modify-private" in REQUIRED_SCOPES
    assert "user-read-email" in REQUIRED_SCOPES
    assert "user-top-read" in REQUIRED_SCOPES


def test_parse_scopes():
    assert parse_scopes("a b  c") == ["a", "b", "c"]
    assert parse_scopes(["x", "y"]) == ["x", "y"]
    assert parse_scopes(None) == []


def test_missing_scopes():
    granted = ["playlist-modify-private", "user-read-private"]
    miss = missing_scopes(granted)
    assert "playlist-modify-public" in miss
    assert "user-read-email" in miss
    assert "playlist-modify-private" not in miss


def test_has_playlist_permission():
    assert has_playlist_permission(["playlist-modify-private"]) is True
    assert has_playlist_permission(["playlist-modify-public"]) is True
    assert has_playlist_permission(["user-read-private"]) is False
    assert has_playlist_permission([]) is False


def test_403_hint_is_actionable():
    assert "allowlist" in _PLAYLIST_403_HINT.lower() or "allowlisted" in _PLAYLIST_403_HINT.lower() or "allowlist" in _PLAYLIST_403_HINT
    assert "auth --force" in _PLAYLIST_403_HINT
    assert "logout" in _PLAYLIST_403_HINT
    assert "Development" in _PLAYLIST_403_HINT


def test_is_forbidden():
    exc = SpotifyException(403, -1, "Forbidden", headers={})
    assert _is_forbidden(exc) is True
    exc2 = SpotifyException(401, -1, "Unauthorized", headers={})
    assert _is_forbidden(exc2) is False
