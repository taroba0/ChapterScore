"""Tests for Streamlit browser OAuth helpers."""

from chapterscore.spotify.web_auth import (
    build_authorize_url,
    normalize_redirect_uri,
    process_oauth_callback,
    token_is_expired,
)


def test_normalize_redirect_uri_trailing_slash():
    assert normalize_redirect_uri("https://foo.streamlit.app") == "https://foo.streamlit.app/"
    assert normalize_redirect_uri("https://foo.streamlit.app/") == "https://foo.streamlit.app/"
    assert normalize_redirect_uri("http://localhost:8501") == "http://localhost:8501/"


def test_build_authorize_url_contains_scopes(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid123")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")
    from chapterscore.config import reload_settings

    reload_settings()
    url, state = build_authorize_url(redirect_uri="http://localhost:8501/")
    assert "accounts.spotify.com/authorize" in url
    assert "client_id=cid123" in url
    assert "playlist-modify-private" in url
    assert "user-read-email" in url
    assert "show_dialog=true" in url
    assert state
    assert len(state) >= 8


def test_process_oauth_callback_state_mismatch():
    session = {"spotify_oauth_state": "abc", "spotify_redirect_uri_used": "http://localhost:8501/"}
    handled, err = process_oauth_callback(
        query_params={"code": "x", "state": "zzz"},
        session_state=session,
    )
    assert handled is True
    assert err and "state" in err.lower()


def test_process_oauth_callback_user_denied():
    session = {}
    handled, err = process_oauth_callback(
        query_params={"error": "access_denied", "error_description": "User denied"},
        session_state=session,
    )
    assert handled is True
    assert err and "denied" in err.lower()


def test_process_oauth_callback_noop():
    handled, err = process_oauth_callback(query_params={}, session_state={})
    assert handled is False
    assert err is None


def test_token_is_expired():
    assert token_is_expired(None) is True
    assert token_is_expired({}) is True
    assert token_is_expired({"access_token": "t", "expires_at": 0}) is True
    assert token_is_expired({"access_token": "t", "expires_at": 9_999_999_999}) is False
