"""Ensure generate_playlist accepts an injected Spotify client (web OAuth)."""

import inspect
from unittest.mock import MagicMock, patch

from chapterscore.models import BookMetadata, BookVibeAnalysis, LyricsPreference, Mode
from chapterscore.pipeline import generate_playlist


def test_generate_playlist_signature_accepts_spotify_client():
    sig = inspect.signature(generate_playlist)
    assert "spotify_client" in sig.parameters
    param = sig.parameters["spotify_client"]
    assert param.default is None


def test_generate_playlist_uses_injected_client_not_get_spotify():
    fake_sp = MagicMock(name="session_spotify")
    book = BookMetadata(title="Dune", authors=["Frank Herbert"], source="test")
    analysis = BookVibeAnalysis(
        book_title="Dune",
        overall_mood="epic",
        overall_energy=0.7,
        playlist_title_suggestion="Dune Score",
        playlist_description="Epic desert.",
    )
    tracks = [
        MagicMock(
            uri="spotify:track:1",
            name="Theme",
            artist_str="Zimmer",
            chapter_number=None,
            vibe_note="",
        )
    ]
    # RankedTrack-like attributes used by description builder
    from chapterscore.models import RankedTrack

    real_tracks = [
        RankedTrack(
            uri="spotify:track:abc",
            id="abc",
            name="Theme",
            artists=["Zimmer"],
            score=50,
        )
    ]
    playlist = MagicMock(url="https://open.spotify.com/playlist/x", name="Dune Score", track_count=1)

    with (
        patch("chapterscore.pipeline.fetch_book", return_value=book),
        patch("chapterscore.pipeline.analyze_book_vibe", return_value=analysis),
        patch("chapterscore.pipeline.get_spotify") as mock_get_spotify,
        patch("chapterscore.pipeline.select_tracks_for_analysis", return_value=real_tracks) as mock_select,
        patch("chapterscore.pipeline.create_playlist_from_tracks", return_value=playlist) as mock_create,
    ):
        result = generate_playlist(
            "Dune",
            mode=Mode.OVERALL,
            lyrics=LyricsPreference.INSTRUMENTAL_ONLY,
            dry_run=False,
            spotify_client=fake_sp,
            min_hours=0,
            min_tracks=1,
        )

    mock_get_spotify.assert_not_called()
    mock_select.assert_called_once()
    assert mock_select.call_args[0][0] is fake_sp
    mock_create.assert_called_once()
    assert mock_create.call_args[0][0] is fake_sp
    assert result.playlist is playlist


def test_generate_playlist_cli_path_calls_get_spotify_when_no_client():
    book = BookMetadata(title="Dune", authors=["Frank Herbert"], source="test")
    analysis = BookVibeAnalysis(
        book_title="Dune",
        overall_mood="epic",
        overall_energy=0.7,
        playlist_title_suggestion="Dune Score",
    )
    from chapterscore.models import RankedTrack

    real_tracks = [
        RankedTrack(uri="spotify:track:abc", id="abc", name="Theme", artists=["Z"], score=50)
    ]
    playlist = MagicMock(url="https://open.spotify.com/playlist/x", name="P", track_count=1)
    fake_sp = MagicMock(name="cli_spotify")

    with (
        patch("chapterscore.pipeline.fetch_book", return_value=book),
        patch("chapterscore.pipeline.analyze_book_vibe", return_value=analysis),
        patch("chapterscore.pipeline.get_spotify", return_value=fake_sp) as mock_get,
        patch("chapterscore.pipeline.select_tracks_for_analysis", return_value=real_tracks),
        patch("chapterscore.pipeline.create_playlist_from_tracks", return_value=playlist),
    ):
        generate_playlist(
            "Dune",
            dry_run=False,
            min_hours=0,
            min_tracks=1,
            # spotify_client omitted → CLI behavior
        )

    mock_get.assert_called_once()
