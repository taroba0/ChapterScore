"""Tests for personalization prefs and ranking taste affinity."""

from chapterscore.models import (
    LyricsPreference,
    PersonalizationPrefs,
    RankedTrack,
    SearchQuerySpec,
    TasteStrength,
)
from chapterscore.spotify.personalization import TasteProfile
from chapterscore.spotify.ranking import score_track


def test_taste_strength_limits():
    assert TasteStrength.DISABLE.artist_limit() == 0
    assert TasteStrength.TOP_5.artist_limit() == 5
    assert TasteStrength.TOP_10.artist_limit() == 10
    assert TasteStrength.TOP_15.artist_limit() == 15


def test_personalization_defaults():
    p = PersonalizationPrefs()
    assert p.taste_strength == TasteStrength.TOP_10
    assert p.use_recommendations is True
    assert p.exploration == 40
    assert abs(p.comfort - 0.6) < 1e-6
    assert abs(p.explore - 0.4) < 1e-6


def test_affinity_lookup():
    profile = TasteProfile(
        prefs=PersonalizationPrefs(),
        artist_names=["Hans Zimmer", "Max Richter"],
        artist_affinity={"hans zimmer": 1.0, "max richter": 0.7},
    )
    assert profile.affinity_for_artists(["Hans Zimmer"]) == 1.0
    assert profile.affinity_for_artists(["Someone Else"]) == 0.0
    assert profile.affinity_for_artists(["Max Richter", "Other"]) == 0.7


def _track(name: str, artists: list[str], pop: int = 50) -> RankedTrack:
    return RankedTrack(
        uri="spotify:track:x",
        id="x" + name[:4],
        name=name,
        artists=artists,
        popularity=pop,
        duration_ms=200_000,
        features={"energy": 0.5, "valence": 0.4, "instrumentalness": 0.8},
        matched_query="cinematic score",
    )


def test_comfort_prefers_familiar_artist():
    spec = SearchQuerySpec(query="epic score", energy=0.6)
    familiar = _track("Time", ["Hans Zimmer"], pop=70)
    stranger = _track("Unknown Cue", ["Obscure Composer"], pop=70)

    s_fam = score_track(
        familiar,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        taste_affinity=1.0,
        exploration=10,  # comfort
    )
    s_str = score_track(
        stranger,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        taste_affinity=0.0,
        exploration=10,
    )
    assert s_fam > s_str


def test_exploration_boosts_novelty_relative_to_comfort():
    spec = SearchQuerySpec(query="epic score", energy=0.6)
    familiar = _track("Time", ["Hans Zimmer"], pop=60)
    stranger = _track("New World", ["Fresh Artist"], pop=60)

    gap_comfort = score_track(
        familiar, spec, LyricsPreference.INSTRUMENTAL_ONLY, taste_affinity=1.0, exploration=10
    ) - score_track(
        stranger, spec, LyricsPreference.INSTRUMENTAL_ONLY, taste_affinity=0.0, exploration=10
    )
    gap_explore = score_track(
        familiar, spec, LyricsPreference.INSTRUMENTAL_ONLY, taste_affinity=1.0, exploration=90
    ) - score_track(
        stranger, spec, LyricsPreference.INSTRUMENTAL_ONLY, taste_affinity=0.0, exploration=90
    )
    # Familiarity advantage shrinks as exploration rises
    assert gap_explore < gap_comfort
