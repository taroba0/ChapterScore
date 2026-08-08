"""ChapterScore-specific exceptions."""

from __future__ import annotations


class ChapterScoreError(Exception):
    """Base error with a user-facing message."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n  → {self.hint}"
        return self.message


class ConfigError(ChapterScoreError):
    """Missing or invalid configuration / credentials."""


class BookNotFoundError(ChapterScoreError):
    """Could not resolve the requested book."""


class BookFetchError(ChapterScoreError):
    """Network or parse failure while fetching book data."""


class AnalysisError(ChapterScoreError):
    """Vibe analysis failed (LLM or validation)."""


class SpotifyAuthError(ChapterScoreError):
    """Spotify OAuth / token problems."""


class SpotifyAPIError(ChapterScoreError):
    """Spotify Web API error."""


class TrackSelectionError(ChapterScoreError):
    """Could not find enough suitable tracks."""
