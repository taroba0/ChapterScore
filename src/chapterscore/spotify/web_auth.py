"""
Browser-based Spotify OAuth for the Streamlit web UI.

Flow:
  1. App builds authorize URL (redirect_uri = current Streamlit origin).
  2. User clicks "Login with Spotify" → Spotify consent screen.
  3. Spotify redirects back to the app with ?code=...&state=...
  4. App exchanges code for tokens server-side (client secret never hits the browser).
  5. Tokens live in st.session_state for this browser session.

CLI auth (file cache + localhost:8888) is unchanged and independent.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import spotipy

from chapterscore.config import get_settings
from chapterscore.exceptions import ConfigError, SpotifyAuthError
from chapterscore.spotify.auth import (
    REQUIRED_SCOPES,
    has_playlist_permission,
    parse_scopes,
    required_scope_string,
)

logger = logging.getLogger(__name__)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

# session_state keys
SS_TOKEN = "spotify_token"
SS_OAUTH_STATE = "spotify_oauth_state"
SS_REDIRECT_URI = "spotify_redirect_uri_used"
SS_USER = "spotify_user"


def load_streamlit_secrets_into_env() -> None:
    """
    Copy Streamlit Cloud secrets into os.environ so pydantic Settings can see them.

    Safe to call repeatedly. No-op when not running under Streamlit or secrets missing.
    """
    import os

    try:
        import streamlit as st
    except ImportError:
        return

    try:
        secrets_map = st.secrets  # type: ignore[attr-defined]
    except Exception:
        return

    # st.secrets behaves like a dict mapping
    keys = (
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_REDIRECT_URI",
        "SPOTIFY_WEB_REDIRECT_URI",
        "XAI_API_KEY",
        "XAI_MODEL",
        "GOOGLE_BOOKS_API_KEY",
    )
    for key in keys:
        try:
            val = secrets_map.get(key) if hasattr(secrets_map, "get") else secrets_map[key]
        except Exception:
            continue
        if val is not None and str(val).strip():
            os.environ.setdefault(key, str(val).strip())


def normalize_redirect_uri(uri: str) -> str:
    """
    Normalize redirect URI for Spotify exact-match rules.

    We always use a trailing slash on the app root (Streamlit serves `/`).
    """
    uri = (uri or "").strip()
    if not uri:
        return uri
    # Strip query/fragment if someone pasted a full callback URL
    uri = uri.split("?")[0].split("#")[0]
    if not uri.endswith("/"):
        uri += "/"
    return uri


def detect_app_base_url() -> str:
    """
    Best-effort base URL of the running Streamlit app.

    Priority:
      1. SPOTIFY_WEB_REDIRECT_URI or SPOTIFY_REDIRECT_URI env (if looks like streamlit/http app)
      2. Streamlit request headers (Host / X-Forwarded-*)
      3. Local default http://localhost:8501/
    """
    settings = get_settings()
    # Explicit web redirect wins (set this on Streamlit Cloud secrets)
    for attr in ("spotify_web_redirect_uri",):
        explicit = getattr(settings, attr, "") or ""
        if explicit:
            return normalize_redirect_uri(explicit)

    env_web = __import__("os").environ.get("SPOTIFY_WEB_REDIRECT_URI", "").strip()
    if env_web:
        return normalize_redirect_uri(env_web)

    # Prefer headers when available (Cloud + local)
    try:
        import streamlit as st

        headers = {}
        try:
            # Streamlit >= 1.30
            headers = dict(st.context.headers)  # type: ignore[attr-defined]
        except Exception:
            headers = {}

        def _h(name: str) -> str:
            for k, v in headers.items():
                if k.lower() == name.lower():
                    return str(v).split(",")[0].strip()
            return ""

        host = _h("x-forwarded-host") or _h("host")
        proto = _h("x-forwarded-proto") or ("https" if host and "streamlit" in host else "http")
        # Local streamlit often reports host localhost:8501
        if host:
            # Drop default ports noise
            return normalize_redirect_uri(f"{proto}://{host}/")
    except Exception:
        pass

    # Local Streamlit default
    return normalize_redirect_uri("http://localhost:8501/")


def resolve_web_redirect_uri() -> str:
    """
    Redirect URI used for the *web* OAuth flow.

    Note: CLI still uses SPOTIFY_REDIRECT_URI (default http://127.0.0.1:8888/callback).
    Web uses the Streamlit app origin so Spotify can bounce back into the UI.
    """
    settings = get_settings()
    if getattr(settings, "spotify_web_redirect_uri", ""):
        return normalize_redirect_uri(settings.spotify_web_redirect_uri)
    env_web = __import__("os").environ.get("SPOTIFY_WEB_REDIRECT_URI", "").strip()
    if env_web:
        return normalize_redirect_uri(env_web)
    return detect_app_base_url()


def build_authorize_url(*, redirect_uri: str | None = None, state: str | None = None) -> tuple[str, str]:
    """
    Return (authorize_url, state).

    ``state`` is a CSRF token the caller must store and verify on callback.
    """
    settings = get_settings()
    if not settings.spotify_client_id:
        raise ConfigError(
            "SPOTIFY_CLIENT_ID is not set.",
            hint="Add it to .env (local) or Streamlit Secrets (Cloud).",
        )
    redirect = normalize_redirect_uri(redirect_uri or resolve_web_redirect_uri())
    state = state or secrets.token_urlsafe(24)
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": required_scope_string(),
        "state": state,
        "show_dialog": "true",
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}", state


def exchange_code_for_token(*, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange authorization code for access + refresh tokens."""
    settings = get_settings()
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        raise ConfigError(
            "Spotify client credentials missing.",
            hint="Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in secrets/.env",
        )
    redirect = normalize_redirect_uri(redirect_uri)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                SPOTIFY_TOKEN_URL,
                data=data,
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code >= 400:
                detail = resp.text[:400]
                raise SpotifyAuthError(
                    f"Token exchange failed ({resp.status_code}): {detail}",
                    hint=(
                        "Redirect URI must match Spotify Dashboard exactly "
                        f"(used: {redirect}). Also confirm client id/secret."
                    ),
                )
            payload = resp.json()
    except SpotifyAuthError:
        raise
    except Exception as exc:
        raise SpotifyAuthError(f"Token exchange request failed: {exc}") from exc

    return _normalize_token(payload)


def refresh_access_token(token_info: dict[str, Any]) -> dict[str, Any]:
    """Refresh an expired access token using the refresh_token."""
    settings = get_settings()
    refresh = token_info.get("refresh_token")
    if not refresh:
        raise SpotifyAuthError(
            "No refresh token available — please log in with Spotify again.",
        )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                SPOTIFY_TOKEN_URL,
                data=data,
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code >= 400:
                raise SpotifyAuthError(
                    f"Token refresh failed ({resp.status_code}): {resp.text[:300]}",
                    hint="Click Login with Spotify again.",
                )
            payload = resp.json()
    except SpotifyAuthError:
        raise
    except Exception as exc:
        raise SpotifyAuthError(f"Token refresh request failed: {exc}") from exc

    # Spotify may omit refresh_token on refresh — keep the old one
    if not payload.get("refresh_token"):
        payload["refresh_token"] = refresh
    if not payload.get("scope") and token_info.get("scope"):
        payload["scope"] = token_info["scope"]
    return _normalize_token(payload)


def _normalize_token(payload: dict[str, Any]) -> dict[str, Any]:
    """Add expires_at (unix) for easy expiry checks."""
    out = dict(payload)
    expires_in = int(out.get("expires_in") or 3600)
    # Refresh 60s early
    out["expires_at"] = time.time() + max(30, expires_in - 60)
    out["scope"] = out.get("scope") or required_scope_string()
    return out


def token_is_expired(token_info: dict[str, Any] | None) -> bool:
    if not token_info or not token_info.get("access_token"):
        return True
    exp = token_info.get("expires_at")
    if exp is None:
        return False
    return time.time() >= float(exp)


def ensure_fresh_token(token_info: dict[str, Any]) -> dict[str, Any]:
    """Return a non-expired token dict, refreshing if needed."""
    if token_is_expired(token_info):
        return refresh_access_token(token_info)
    return token_info


def spotify_client_from_token(token_info: dict[str, Any]) -> spotipy.Spotify:
    """Build a spotipy client that uses the given access token (no file cache)."""
    token_info = ensure_fresh_token(token_info)
    access = token_info["access_token"]
    settings = get_settings()
    timeout = float(getattr(settings, "chapterscore_spotify_requests_timeout", 8.0))
    return spotipy.Spotify(
        auth=access,
        requests_timeout=timeout,
        status_forcelist=(500, 502, 503, 504),
        retries=1,
        status_retries=1,
        backoff_factor=0.3,
    )


def fetch_current_user(token_info: dict[str, Any]) -> dict[str, Any]:
    sp = spotify_client_from_token(token_info)
    return sp.current_user()


def session_is_authenticated(session_state: Any) -> bool:
    tok = session_state.get(SS_TOKEN)
    return bool(tok and tok.get("access_token"))


def session_token_scopes(session_state: Any) -> list[str]:
    tok = session_state.get(SS_TOKEN) or {}
    return parse_scopes(tok.get("scope"))


def session_has_playlist_permission(session_state: Any) -> bool:
    return has_playlist_permission(session_token_scopes(session_state))


def clear_session_auth(session_state: Any) -> None:
    for key in (SS_TOKEN, SS_OAUTH_STATE, SS_REDIRECT_URI, SS_USER):
        if key in session_state:
            del session_state[key]


def process_oauth_callback(
    *,
    query_params: dict[str, Any],
    session_state: Any,
) -> tuple[bool, str | None]:
    """
    Handle Spotify redirect query params.

    Returns (handled, error_message).
    ``handled=True`` means a code/error was present (caller should clear query params).
    """
    # Streamlit query_params values may be str or list
    def _one(key: str) -> str | None:
        if key not in query_params:
            return None
        val = query_params[key]
        if isinstance(val, (list, tuple)):
            return str(val[0]) if val else None
        return str(val) if val is not None else None

    err = _one("error")
    code = _one("code")
    state = _one("state")

    if not err and not code:
        return False, None

    if err:
        desc = _one("error_description") or err
        return True, f"Spotify login was denied or failed: {desc}"

    expected = session_state.get(SS_OAUTH_STATE)
    if expected and state and state != expected:
        return True, "OAuth state mismatch — please try Login with Spotify again."

    redirect_uri = session_state.get(SS_REDIRECT_URI) or resolve_web_redirect_uri()
    try:
        token = exchange_code_for_token(code=code or "", redirect_uri=redirect_uri)
        session_state[SS_TOKEN] = token
        try:
            session_state[SS_USER] = fetch_current_user(token)
        except Exception as exc:
            logger.warning("Could not fetch Spotify profile after login: %s", exc)
            session_state[SS_USER] = None
        # Clean transient oauth keys
        session_state.pop(SS_OAUTH_STATE, None)
        return True, None
    except Exception as exc:
        return True, str(exc)


def get_session_spotify(session_state: Any) -> spotipy.Spotify:
    """
    Return an authenticated Spotify client from session_state tokens.

    Refreshes the access token in place when expired.
    """
    tok = session_state.get(SS_TOKEN)
    if not tok or not tok.get("access_token"):
        raise SpotifyAuthError(
            "Not logged in to Spotify.",
            hint="Click **Login with Spotify** in the sidebar.",
        )
    try:
        fresh = ensure_fresh_token(tok)
    except SpotifyAuthError:
        clear_session_auth(session_state)
        raise
    session_state[SS_TOKEN] = fresh
    return spotify_client_from_token(fresh)


def login_button_meta(session_state: Any) -> dict[str, str]:
    """
    Prepare authorize URL + store state/redirect for the upcoming login.

    Call this when rendering the Login button so state is ready.
    """
    redirect = resolve_web_redirect_uri()
    url, state = build_authorize_url(redirect_uri=redirect)
    session_state[SS_OAUTH_STATE] = state
    session_state[SS_REDIRECT_URI] = redirect
    return {
        "authorize_url": url,
        "redirect_uri": redirect,
        "scopes": required_scope_string(),
    }
