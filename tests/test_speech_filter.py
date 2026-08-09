"""Hard filter: podcasts, speech, commentary, audiobook-style content."""

from chapterscore.models import LyricsPreference, RankedTrack
from chapterscore.spotify.ranking import (
    filter_music_only,
    is_speech_or_non_music,
    passes_content_filter,
    passes_lyrics_filter,
)


def _t(
    name: str,
    artists: list[str] | None = None,
    *,
    album: str = "",
    features: dict | None = None,
    matched_query: str = "cinematic score",
    id_: str = "1",
) -> RankedTrack:
    return RankedTrack(
        uri=f"spotify:track:{id_}",
        id=id_,
        name=name,
        artists=artists or ["Artist"],
        album=album,
        popularity=50,
        duration_ms=200_000,
        features=features or {},
        matched_query=matched_query,
    )


def test_rejects_podcast_title():
    t = _t("Episode 42: Book Club Deep Dive", album="The Literary Podcast")
    assert is_speech_or_non_music(t) is True
    assert passes_content_filter(t) is False


def test_rejects_interview():
    t = _t("Author Interview with George Orwell", artists=["BBC Radio"])
    assert is_speech_or_non_music(t) is True


def test_rejects_commentary_track():
    t = _t("Director's Commentary", album="Film Extras")
    assert is_speech_or_non_music(t) is True


def test_rejects_audiobook_narration():
    t = _t("Chapter 1 — Narrated by the Author", album="Audiobook Edition")
    assert is_speech_or_non_music(t) is True


def test_rejects_spoken_word():
    t = _t("Spoken Word Monologue", artists=["Poet X"])
    assert is_speech_or_non_music(t) is True


def test_rejects_high_speechiness():
    t = _t(
        "Track Without Keywords",
        features={"speechiness": 0.72, "instrumentalness": 0.05},
    )
    assert is_speech_or_non_music(t) is True


def test_rejects_mid_speechiness_low_instrumental():
    t = _t(
        "Ambiguous Talk Clip",
        features={"speechiness": 0.42, "instrumentalness": 0.1},
    )
    assert is_speech_or_non_music(t) is True


def test_allows_normal_music():
    t = _t(
        "Cornfield Chase",
        artists=["Hans Zimmer"],
        album="Interstellar (Original Motion Picture Soundtrack)",
        features={"speechiness": 0.03, "instrumentalness": 0.95},
    )
    assert is_speech_or_non_music(t) is False
    assert passes_content_filter(t) is True


def test_allows_sung_vocals_under_allow_lyrics():
    """Speech filter must not block normal songs with lyrics."""
    t = _t(
        "Love Song",
        artists=["Indie Band"],
        features={"speechiness": 0.08, "instrumentalness": 0.05},
    )
    assert is_speech_or_non_music(t) is False
    assert passes_lyrics_filter(t, LyricsPreference.ALLOW_LYRICS) is True


def test_speech_blocked_even_under_allow_lyrics():
    """Critical: allow-lyrics mode still hard-blocks podcasts/speech."""
    t = _t(
        "Podcast Episode 12",
        album="True Crime Podcast",
        features={"speechiness": 0.8, "instrumentalness": 0.0},
    )
    assert passes_lyrics_filter(t, LyricsPreference.ALLOW_LYRICS) is False
    assert passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY) is False


def test_filter_music_only_strips_speech_from_list():
    good = _t(
        "Main Theme",
        artists=["Max Richter"],
        id_="g1",
        features={"speechiness": 0.02, "instrumentalness": 0.9},
    )
    bad = _t(
        "Interview with the Composer",
        id_="b1",
        features={"speechiness": 0.6, "instrumentalness": 0.0},
    )
    out = filter_music_only([good, bad])
    assert len(out) == 1
    assert out[0].id == "g1"


def test_podcast_artist_name():
    t = _t("Weekly Discussion", artists=["Bookish Podcast Network"])
    assert is_speech_or_non_music(t) is True
