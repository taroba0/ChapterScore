"""Tests for track ranking and multi-level lyrics filtering."""

from chapterscore.models import LyricsPreference, RankedTrack, SearchQuerySpec
from chapterscore.spotify.ranking import (
    InstrumentalStrictness,
    is_likely_instrumental,
    passes_lyrics_filter,
    score_track,
    select_diverse,
)


def _track(
    id_: str,
    name: str,
    artists: list[str],
    popularity: int = 50,
    features: dict | None = None,
    matched_query: str = "",
    **kwargs,
) -> RankedTrack:
    return RankedTrack(
        uri=f"spotify:track:{id_}",
        id=id_,
        name=name,
        artists=artists,
        popularity=popularity,
        duration_ms=210_000,
        features=features or {},
        matched_query=matched_query,
        **kwargs,
    )


def test_instrumental_from_features():
    t = _track("1", "Theme", ["Composer"], features={"instrumentalness": 0.95, "speechiness": 0.02})
    assert is_likely_instrumental(t) is True


def test_vocal_from_features():
    t = _track("2", "Love Song", ["Pop Star"], features={"instrumentalness": 0.05, "speechiness": 0.1})
    assert is_likely_instrumental(t) is False


def test_instrumental_from_title():
    t = _track("3", "Main Theme (Instrumental)", ["Score Artist"])
    assert is_likely_instrumental(t) is True


def test_strict_rejects_clear_vocals():
    vocal = _track(
        "4",
        "Hit Single",
        ["Singer"],
        features={"instrumentalness": 0.1, "speechiness": 0.12},
    )
    assert (
        passes_lyrics_filter(vocal, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.STRICT)
        is False
    )


def test_strict_accepts_high_instrumentalness():
    score = _track(
        "5",
        "Battle Theme",
        ["Orchestra"],
        features={"instrumentalness": 0.9, "speechiness": 0.01},
        matched_query="epic orchestral score",
    )
    assert (
        passes_lyrics_filter(score, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.STRICT)
        is True
    )


def test_missing_features_allowed_when_query_flavored_strict():
    """Without audio features, soundtrack-flavored searches still pass STRICT."""
    t = _track(
        "nf1",
        "Arrival of the Birds",
        ["The Cinematic Orchestra"],
        features={},
        matched_query="cinematic orchestral soundtrack",
    )
    assert (
        passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.STRICT)
        is True
    )


def test_missing_features_blocked_without_cues_strict():
    t = _track(
        "nf2",
        "Random Pop Song",
        ["Someone"],
        features={},
        matched_query="happy music",
    )
    assert (
        passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.STRICT)
        is False
    )


def test_relaxed_allows_missing_features():
    t = _track(
        "nf3",
        "Mysterious Cue",
        ["Studio Ensemble"],
        features={},
        matched_query="tense thriller",
    )
    assert (
        passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.RELAXED)
        is True
    )


def test_permissive_almost_always_passes():
    t = _track("p1", "Some Track", ["Artist"], features={"instrumentalness": 0.2})
    assert (
        passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.PERMISSIVE)
        is True
    )


def test_lyrics_filter_rejects_karaoke():
    t = _track("6", "Someone Like You (Karaoke Version)", ["Tribute Band"], features={})
    assert passes_lyrics_filter(t, LyricsPreference.NO) is False


def test_rejects_royalty_free_junk():
    t = _track("rf", "Epic Battle Royalty Free", ["EnergySound"], features={})
    assert passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY) is False


def test_score_artist_boost():
    spec = SearchQuerySpec(query="epic film score", energy=0.7)
    zimmer = _track(
        "z",
        "Time",
        ["Hans Zimmer"],
        popularity=70,
        features={},
        matched_query="epic film score instrumental",
    )
    unknown = _track(
        "u",
        "Epic Battle Cue",
        ["Unknown Stock Artist"],
        popularity=0,
        features={},
        matched_query="epic film score instrumental",
    )
    assert score_track(zimmer, spec, LyricsPreference.INSTRUMENTAL_ONLY) > score_track(
        unknown, spec, LyricsPreference.INSTRUMENTAL_ONLY
    )


def test_score_prefers_feature_fit():
    spec = SearchQuerySpec(query="dark ambient", energy=0.2, valence=0.15)
    good = _track(
        "a",
        "Dark Ambient Drone",
        ["Artist A"],
        popularity=40,
        features={"energy": 0.22, "valence": 0.18, "instrumentalness": 0.9},
        matched_query="dark ambient instrumental",
    )
    bad = _track(
        "b",
        "Party Anthem",
        ["Artist B"],
        popularity=90,
        features={"energy": 0.95, "valence": 0.9, "instrumentalness": 0.0},
        matched_query="dark ambient instrumental",
    )
    s_good = score_track(good, spec, LyricsPreference.NO)
    s_bad = score_track(bad, spec, LyricsPreference.NO)
    assert s_good > s_bad


def test_score_tolerates_low_keyword_overlap():
    """Film scores rarely share query tokens — should still score decently."""
    spec = SearchQuerySpec(
        query="tense desert empire intrigue instrumental",
        energy=0.55,
        valence=0.3,
        mood_keywords=["epic", "mysterious"],
    )
    t = _track(
        "sc",
        "Dream of Arrakeen",
        ["Hans Zimmer"],
        popularity=65,
        features={"energy": 0.5, "valence": 0.28, "instrumentalness": 0.92},
        matched_query="epic film score instrumental",
    )
    s = score_track(t, spec, LyricsPreference.INSTRUMENTAL_ONLY)
    assert s >= 35  # must not collapse due to zero lexical overlap


def test_select_diverse_limits_artists():
    spec = SearchQuerySpec(query="epic orchestral", energy=0.8)
    candidates = []
    for i in range(6):
        t = _track(
            f"id{i}",
            f"Track {i}",
            ["Same Artist"] if i < 4 else [f"Other {i}"],
            popularity=60 + i,
            features={"energy": 0.8, "valence": 0.5},
        )
        t.score = score_track(t, spec, LyricsPreference.NO)
        candidates.append(t)

    chosen = select_diverse(candidates, n=4, max_per_artist=2)
    same_count = sum(1 for t in chosen if t.artists == ["Same Artist"])
    assert same_count <= 2
    assert len(chosen) == 4


def test_select_diverse_dedupes_ids():
    t1 = _track("x", "Song", ["A"], popularity=80, features={"energy": 0.5})
    t2 = _track("x", "Song", ["A"], popularity=80, features={"energy": 0.5})
    t3 = _track("y", "Other", ["B"], popularity=70, features={"energy": 0.5})
    for t in (t1, t2, t3):
        t.score = 50.0
    chosen = select_diverse([t1, t2, t3], n=3)
    assert len(chosen) == 2


def test_mixed_lyrics_mode_passes_vocals():
    vocal = _track(
        "v1",
        "Hit Single",
        ["Singer"],
        features={"instrumentalness": 0.05, "speechiness": 0.1},
    )
    assert passes_lyrics_filter(vocal, LyricsPreference.NO) is True
