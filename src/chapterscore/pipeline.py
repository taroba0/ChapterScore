"""Orchestrates book fetch → vibe analysis → track selection → playlist creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from chapterscore.analysis.grok import analyze_book_vibe
from chapterscore.books.aggregator import fetch_book
from chapterscore.config import get_settings
from chapterscore.exceptions import TrackSelectionError
from chapterscore.models import (
    BookMetadata,
    BookVibeAnalysis,
    LyricsPreference,
    Mode,
    PlaylistResult,
    RankedTrack,
)
from chapterscore.spotify.auth import get_spotify
from chapterscore.spotify.playlist import build_playlist_description, create_playlist_from_tracks
from chapterscore.spotify.selection import select_tracks_for_analysis

if TYPE_CHECKING:
    import spotipy

ProgressCb = Callable[[str], None]


def _noop(_: str) -> None:
    pass


@dataclass
class GenerateResult:
    book: BookMetadata
    analysis: BookVibeAnalysis
    tracks: list[RankedTrack] = field(default_factory=list)
    playlist: PlaylistResult | None = None


def generate_playlist(
    title: str,
    *,
    author: str | None = None,
    isbn: str | None = None,
    mode: Mode = Mode.OVERALL,
    lyrics: LyricsPreference = LyricsPreference.NO,
    tracks: int | None = None,
    tracks_per_chapter: int | None = None,
    min_tracks: int | None = None,
    min_hours: float | None = None,
    public: bool = False,
    playlist_name: str | None = None,
    dry_run: bool = False,
    use_cache: bool = True,
    progress: ProgressCb = _noop,
    spotify_client: spotipy.Spotify | None = None,
) -> GenerateResult:
    """
    Full ChapterScore pipeline.

    Parameters
    ----------
    dry_run:
        If True, fetch book + analyze vibe only (no Spotify search/playlist).
    spotify_client:
        Optional pre-authenticated Spotify client (e.g. Streamlit browser OAuth).
        When ``None`` (CLI default), authenticates via ``get_spotify()`` file cache.
    """
    settings = get_settings()

    progress("Fetching book metadata…")
    book = fetch_book(
        title,
        author=author,
        isbn=isbn,
        use_cache=use_cache,
        want_chapters=(mode == Mode.CHAPTER),
    )
    progress(f"Found: {book.display_name}  [{book.source}]")

    progress("Analyzing literary vibe with Grok…")
    analysis = analyze_book_vibe(
        book,
        mode=mode,
        lyrics=lyrics,
        use_cache=use_cache,
    )
    progress(f"Mood: {analysis.overall_mood} · Energy: {analysis.overall_energy:.2f}")

    if dry_run:
        return GenerateResult(book=book, analysis=analysis, tracks=[])

    progress("Connecting to Spotify…")
    # Web: use session client from browser OAuth. CLI: file-token OAuth.
    if spotify_client is not None:
        sp = spotify_client
        progress("Using provided Spotify session client")
    else:
        sp = get_spotify()

    n_overall = tracks or settings.chapterscore_tracks_overall
    n_chapter = tracks_per_chapter or settings.chapterscore_tracks_per_chapter

    progress("Searching and ranking tracks (progressive fallback enabled)…")
    selected = select_tracks_for_analysis(
        sp,
        analysis,
        mode=mode,
        lyrics=lyrics,
        tracks_overall=n_overall,
        tracks_per_chapter=n_chapter,
        min_tracks=min_tracks,
        min_hours=min_hours,
        progress=progress,
    )

    if not selected:
        raise TrackSelectionError(
            "Could not find any suitable tracks for this book.",
            hint=(
                "This is unexpected after progressive fallback. "
                "Check Spotify auth, network, and try again. "
                "If it persists, your market may have limited catalogue access."
            ),
        )

    lyrics_label = {
        LyricsPreference.YES: "with lyrics",
        LyricsPreference.NO: "mixed",
        LyricsPreference.INSTRUMENTAL_ONLY: "instrumental",
    }[lyrics]

    name = playlist_name or analysis.playlist_title_suggestion or f"ChapterScore: {book.title}"
    if mode == Mode.CHAPTER and "chapter" not in name.lower():
        name = f"{name} (Chapters)"

    description = build_playlist_description(
        book_title=book.title,
        authors=book.authors,
        mode=mode,
        analysis_description=analysis.playlist_description,
        tracks=selected,
        lyrics_label=lyrics_label,
    )

    visibility = "public" if public else "private"
    progress(f"Creating {visibility} playlist “{name}” with {len(selected)} tracks…")
    playlist = create_playlist_from_tracks(
        sp,
        selected,
        name=name,
        description=description,
        public=public,
        book_title=book.title,
        mode=mode,
    )
    progress(f"Done → {playlist.url}")

    return GenerateResult(
        book=book,
        analysis=analysis,
        tracks=selected,
        playlist=playlist,
    )
# force redeploy Sat Aug  8 16:39:48 HKT 2026
