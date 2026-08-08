"""Spotify OAuth with secure on-disk token cache and automatic refresh."""

from __future__ import annotations

import logging
from typing import Any

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

from chapterscore.config import get_settings
from chapterscore.exceptions import ConfigError, SpotifyAuthError

logger = logging.getLogger(__name__)
logging.getLogger("spotipy").setLevel(logging.CRITICAL)

# Required scopes for ChapterScore playlist creation + profile diagnostics.
REQUIRED_SCOPES: tuple[str, ...] = (
    "playlist-modify-public",
    "playlist-modify-private",
    "user-read-private",
    "user-read-email",
)

PLAYLIST_SCOPES: tuple[str, ...] = (
    "playlist-modify-public",
    "playlist-modify-private",
)


def required_scope_string() -> str:
    """Canonical space-separated scope string (order stable)."""
    settings = get_settings()
    # Merge config + required so env overrides can add scopes but never drop required ones
    configured = (settings.spotify_scope or "").split()
    merged: list[str] = []
    for s in list(REQUIRED_SCOPES) + configured:
        if s and s not in merged:
            merged.append(s)
    return " ".join(merged)


def _req_timeout() -> float:
    return float(getattr(get_settings(), "chapterscore_spotify_requests_timeout", 8.0))


def _oauth(*, show_dialog: bool = False) -> SpotifyOAuth:
    settings = get_settings()
    missing = settings.missing_required(need_spotify=True, need_xai=False)
    if missing:
        raise ConfigError(
            f"Missing Spotify credentials: {', '.join(missing)}",
            hint=(
                "Create an app at https://developer.spotify.com/dashboard, "
                "set the redirect URI to http://127.0.0.1:8888/callback, "
                "and put the client id/secret in your .env file."
            ),
        )

    cache_handler = CacheFileHandler(cache_path=str(settings.spotify_token_path))
    return SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=required_scope_string(),
        cache_handler=cache_handler,
        open_browser=True,
        # show_dialog=True forces Spotify to re-prompt consent (new scopes)
        show_dialog=show_dialog,
        requests_timeout=_req_timeout(),
    )


def parse_scopes(scope_value: str | list[str] | None) -> list[str]:
    if scope_value is None:
        return []
    if isinstance(scope_value, list):
        return [s for s in scope_value if s]
    return [s for s in str(scope_value).replace(",", " ").split() if s]


def token_scopes(token_info: dict[str, Any] | None = None) -> list[str]:
    """Return scopes from the cached token (if present)."""
    if token_info is None:
        try:
            token_info = _oauth().get_cached_token()
        except Exception:
            return []
    if not token_info:
        return []
    return parse_scopes(token_info.get("scope"))


def missing_scopes(granted: list[str] | None = None) -> list[str]:
    granted_set = set(granted if granted is not None else token_scopes())
    return [s for s in REQUIRED_SCOPES if s not in granted_set]


def has_playlist_permission(granted: list[str] | None = None) -> bool:
    granted_set = set(granted if granted is not None else token_scopes())
    return bool(granted_set & set(PLAYLIST_SCOPES))


def clear_token() -> bool:
    """Delete the cached Spotify token file."""
    path = get_settings().spotify_token_path
    if path.exists():
        path.unlink()
        return True
    return False


def _build_client(auth: SpotifyOAuth) -> spotipy.Spotify:
    return spotipy.Spotify(
        auth_manager=auth,
        requests_timeout=_req_timeout(),
        status_forcelist=(500, 502, 503, 504),
        retries=1,
        status_retries=1,
        backoff_factor=0.3,
    )


def get_spotify(*, force_reauth: bool = False) -> spotipy.Spotify:
    """
    Return an authenticated Spotify client.

    On first run (or force_reauth), opens the browser for OAuth consent with
    the full required scope set and ``show_dialog=True`` so Spotify re-prompts.
    Subsequent runs refresh the token automatically.
    """
    try:
        if force_reauth:
            clear_token()
            auth = _oauth(show_dialog=True)
            logger.info("Forcing fresh Spotify OAuth with scopes: %s", required_scope_string())
            token_info = auth.get_access_token(as_dict=True, check_cache=False)  # type: ignore[call-arg]
            if not token_info:
                # Older spotipy: get_access_token(check_cache=False) may not exist
                token_info = auth.get_access_token(as_dict=True)  # type: ignore[assignment]
            if not token_info:
                raise SpotifyAuthError(
                    "Spotify authorization failed or was cancelled.",
                    hint="Re-run `chapterscore auth --force` and complete the browser login.",
                )
            return _build_client(auth)

        auth = _oauth(show_dialog=False)
        token_info: dict[str, Any] | None = auth.get_cached_token()

        # If token is missing required scopes, force a fresh consent dialog
        if token_info and missing_scopes(token_scopes(token_info)):
            logger.warning(
                "Cached token missing scopes %s — forcing re-auth",
                missing_scopes(token_scopes(token_info)),
            )
            clear_token()
            auth = _oauth(show_dialog=True)
            token_info = None

        if not token_info or auth.is_token_expired(token_info):
            logger.info("Refreshing / obtaining Spotify access token…")
            # If no cache, use show_dialog so user sees full scope list
            if not token_info:
                auth = _oauth(show_dialog=True)
            try:
                token_info = auth.get_access_token(as_dict=True, check_cache=bool(token_info))  # type: ignore[call-arg]
            except TypeError:
                token_info = auth.get_access_token(as_dict=True)  # type: ignore[assignment]
            if not token_info:
                raise SpotifyAuthError(
                    "Spotify authorization failed or was cancelled.",
                    hint="Re-run with `chapterscore auth --force` and complete the browser login.",
                )

        return _build_client(auth)
    except (ConfigError, SpotifyAuthError):
        raise
    except Exception as exc:
        raise SpotifyAuthError(
            f"Could not authenticate with Spotify: {exc}",
            hint=(
                "Ensure SPOTIFY_CLIENT_ID / SECRET are correct and the redirect URI "
                "http://127.0.0.1:8888/callback is whitelisted in your Spotify app. "
                "Then run: chapterscore logout && chapterscore auth --force"
            ),
        ) from exc


def logout() -> bool:
    """Remove the cached Spotify token. Returns True if a file was deleted."""
    return clear_token()


def auth_status() -> dict[str, Any]:
    """Return a small status dict for the CLI `auth` / `doctor` commands."""
    settings = get_settings()
    path = settings.spotify_token_path
    has_creds = bool(settings.spotify_client_id and settings.spotify_client_secret)
    token_cached = path.exists()
    expired = None
    scopes: list[str] = []
    if token_cached:
        try:
            auth = _oauth(show_dialog=False)
            info = auth.get_cached_token()
            expired = bool(info and auth.is_token_expired(info))
            scopes = token_scopes(info)
        except Exception:
            expired = True
    return {
        "has_credentials": has_creds,
        "token_cached": token_cached,
        "token_expired": expired,
        "token_path": str(path),
        "redirect_uri": settings.spotify_redirect_uri,
        "required_scopes": list(REQUIRED_SCOPES),
        "granted_scopes": scopes,
        "missing_scopes": missing_scopes(scopes) if scopes else list(REQUIRED_SCOPES),
        "has_playlist_permission": has_playlist_permission(scopes) if scopes else False,
    }


def diagnose_spotify() -> dict[str, Any]:
    """
    Live diagnostic: current user + granted scopes + playlist permission.
    Performs a lightweight API call when a token is available.
    """
    status = auth_status()
    result: dict[str, Any] = {
        **status,
        "user_id": None,
        "display_name": None,
        "email": None,
        "product": None,
        "api_ok": False,
        "error": None,
    }
    if not status["has_credentials"]:
        result["error"] = "Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET"
        return result
    if not status["token_cached"]:
        result["error"] = "No token — run `chapterscore auth --force`"
        return result

    try:
        sp = get_spotify(force_reauth=False)
        me = sp.current_user()
        result["api_ok"] = True
        result["user_id"] = me.get("id")
        result["display_name"] = me.get("display_name")
        result["email"] = me.get("email")
        result["product"] = me.get("product")  # free / premium
        # Refresh scopes from cache after possible refresh
        result["granted_scopes"] = token_scopes()
        result["missing_scopes"] = missing_scopes(result["granted_scopes"])
        result["has_playlist_permission"] = has_playlist_permission(result["granted_scopes"])
    except Exception as exc:
        result["error"] = str(exc)
    return result
