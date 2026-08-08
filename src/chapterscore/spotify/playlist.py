"""Create and populate Spotify playlists."""

from __future__ import annotations

import logging
import re
from typing import Any

import spotipy
from spotipy.exceptions import SpotifyException

from chapterscore.exceptions import SpotifyAPIError
from chapterscore.models import Mode, PlaylistResult, RankedTrack
from chapterscore.spotify.auth import (
    has_playlist_permission,
    missing_scopes,
    token_scopes,
)

logger = logging.getLogger(__name__)

# Spotify playlist description max is 300 characters
DESC_MAX = 300

_PLAYLIST_403_HINT = (
    "Spotify returned 403 Forbidden while creating the playlist.\n\n"
    "Fix checklist:\n"
    "  1. Spotify Developer Dashboard → your app → User Management:\n"
    "     add THIS Spotify account to the allowlist (Development mode).\n"
    "  2. Run a fresh login so the right scopes are granted:\n"
    "       chapterscore logout\n"
    "       chapterscore auth --force\n"
    "     Confirm the printed scopes include playlist-modify-private.\n"
    "  3. Development mode apps can only be used by allowlisted users\n"
    "     (up to 25). Extended Quota / production is required for others.\n"
    "  4. Prefer a private playlist (default). Public playlists can 403\n"
    "     more often in Development mode — omit --public.\n"
    "  5. Run: chapterscore doctor   (shows user id + granted scopes)"
)


def _truncate(text: str, max_len: int = DESC_MAX) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def build_playlist_description(
    *,
    book_title: str,
    authors: list[str],
    mode: Mode,
    analysis_description: str,
    tracks: list[RankedTrack],
    lyrics_label: str,
) -> str:
    """Craft a Spotify-safe description with optional chapter vibe notes."""
    author = f" by {', '.join(authors)}" if authors else ""
    header = f"ChapterScore · {book_title}{author} · {mode.value} · {lyrics_label}"

    if mode == Mode.CHAPTER:
        notes: list[str] = []
        seen_ch: set[str] = set()
        for t in tracks:
            ch = str(t.chapter_number) if t.chapter_number is not None else None
            if not ch or ch in seen_ch:
                continue
            seen_ch.add(ch)
            note = (t.vibe_note or "").strip()
            if note:
                notes.append(f"Ch{ch}: {note}")
            if len(notes) >= 6:
                break
        body = " | ".join(notes) if notes else (analysis_description or "Chapter-by-chapter soundtrack.")
        return _truncate(f"{header}. {body}")

    body = analysis_description or (
        "A cohesive soundtrack for the book's emotional world — shuffle-friendly."
    )
    return _truncate(f"{header}. {body}")


def _is_forbidden(exc: SpotifyException) -> bool:
    return bool(exc.http_status == 403 or "403" in str(exc) or "Forbidden" in str(exc))


def _create_playlist_api(
    sp: spotipy.Spotify,
    *,
    user_id: str,
    name: str,
    public: bool,
    description: str,
) -> dict[str, Any]:
    """
    Create a playlist for the current user.

    Tries POST /v1/me/playlists first (current-user endpoint), then falls back
    to POST /v1/users/{id}/playlists.
    """
    payload = {
        "name": name[:100],
        "public": bool(public),
        "collaborative": False,
        "description": _truncate(description),
    }

    # Preferred: /me/playlists — no user_id mismatch, works with user tokens
    try:
        return sp._post("me/playlists", payload=payload)  # type: ignore[no-any-return]
    except SpotifyException as exc:
        # 403 is a real permission failure — don't mask it with a second call
        if _is_forbidden(exc):
            raise
        logger.debug(
            "me/playlists failed (%s); trying users/%s/playlists",
            exc,
            user_id,
        )
        return sp.user_playlist_create(  # type: ignore[no-any-return]
            user=user_id,
            name=payload["name"],
            public=payload["public"],
            collaborative=False,
            description=payload["description"],
        )


def create_playlist_from_tracks(
    sp: spotipy.Spotify,
    tracks: list[RankedTrack],
    *,
    name: str,
    description: str,
    public: bool = False,
    book_title: str = "",
    mode: Mode = Mode.OVERALL,
) -> PlaylistResult:
    """
    Create a playlist in the current user's library and add tracks in order.

    Defaults to **private** (public=False). Development-mode Spotify apps
    often 403 on public playlist creation.
    """
    if not tracks:
        raise SpotifyAPIError(
            "No tracks to add to the playlist.",
            hint="Try a different lyrics mode or overall mode for broader search results.",
        )

    # Strict de-dupe before write (id + same-recording variants)
    from chapterscore.spotify.ranking import dedupe_tracks

    tracks = dedupe_tracks(tracks)
    if not tracks:
        raise SpotifyAPIError(
            "No unique tracks left after de-duplication.",
            hint="Try a different lyrics mode or overall mode for broader search results.",
        )

    # Pre-flight scope check against CLI file token when present.
    # Web session tokens may not write the file cache — skip if empty.
    granted = token_scopes()
    if granted and not has_playlist_permission(granted):
        raise SpotifyAPIError(
            "Spotify token is missing playlist-modify scopes.",
            hint=(
                f"Granted: {', '.join(granted) or '(none)'}\n"
                "CLI: chapterscore logout && chapterscore auth --force\n"
                "Web: click Login with Spotify again and accept all permissions."
            ),
        )
    missing = missing_scopes(granted) if granted else []
    if missing:
        logger.warning("Token missing optional/required scopes: %s", missing)

    try:
        me = sp.current_user()
        user_id = me["id"]
    except SpotifyException as exc:
        raise SpotifyAPIError(
            f"Could not fetch Spotify profile: {exc}",
            hint="Run `chapterscore auth --force` and confirm your account is allowlisted.",
        ) from exc

    # Always attempt private first when public was requested but we want resilience:
    # if public=True fails with 403, retry private once.
    attempted_public = bool(public)
    use_public = bool(public)

    playlist: dict[str, Any] | None = None
    last_exc: Exception | None = None

    for attempt_public in ([use_public, False] if use_public else [False]):
        try:
            logger.info(
                "Creating %s playlist %r for user %s",
                "public" if attempt_public else "private",
                name[:100],
                user_id,
            )
            playlist = _create_playlist_api(
                sp,
                user_id=user_id,
                name=name,
                public=attempt_public,
                description=description,
            )
            if attempted_public and not attempt_public:
                logger.warning("Public playlist create failed; created private playlist instead")
            break
        except SpotifyException as exc:
            last_exc = exc
            if _is_forbidden(exc) and attempt_public:
                # Try private next
                continue
            if _is_forbidden(exc):
                raise SpotifyAPIError(
                    "Failed to create Spotify playlist (HTTP 403 Forbidden).",
                    hint=_PLAYLIST_403_HINT,
                ) from exc
            raise SpotifyAPIError(f"Failed to create playlist: {exc}") from exc

    if playlist is None:
        if last_exc and _is_forbidden(last_exc):  # type: ignore[arg-type]
            raise SpotifyAPIError(
                "Failed to create Spotify playlist (HTTP 403 Forbidden).",
                hint=_PLAYLIST_403_HINT,
            ) from last_exc
        raise SpotifyAPIError(f"Failed to create playlist: {last_exc}")

    playlist_id = playlist["id"]
    uris = [t.uri for t in tracks if t.uri]

    try:
        for i in range(0, len(uris), 100):
            sp.playlist_add_items(playlist_id, uris[i : i + 100])
    except SpotifyException as exc:
        if _is_forbidden(exc):
            raise SpotifyAPIError(
                "Playlist was created but adding tracks returned 403 Forbidden.",
                hint=_PLAYLIST_403_HINT + f"\n  Playlist id: {playlist_id}",
            ) from exc
        raise SpotifyAPIError(
            f"Playlist created but failed to add tracks: {exc}",
            hint=f"Playlist id: {playlist_id}",
        ) from exc

    url = (playlist.get("external_urls") or {}).get("spotify") or (
        f"https://open.spotify.com/playlist/{playlist_id}"
    )
    was_public = bool(playlist.get("public", use_public))

    return PlaylistResult(
        id=playlist_id,
        name=playlist.get("name") or name,
        url=url,
        description=_truncate(description),
        track_count=len(uris),
        tracks=tracks,
        mode=mode,
        book_title=book_title,
    )
