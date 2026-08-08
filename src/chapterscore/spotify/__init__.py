"""Spotify integration: OAuth, search, ranking, playlist creation."""

from chapterscore.spotify.auth import diagnose_spotify, get_spotify, required_scope_string
from chapterscore.spotify.playlist import create_playlist_from_tracks
from chapterscore.spotify.ranking import InstrumentalStrictness
from chapterscore.spotify.selection import select_tracks_for_analysis

__all__ = [
    "get_spotify",
    "create_playlist_from_tracks",
    "select_tracks_for_analysis",
    "InstrumentalStrictness",
    "diagnose_spotify",
    "required_scope_string",
]
