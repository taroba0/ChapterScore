"""Reading-companion energy cap, atmosphere weighting, and smooth sequencing."""

from chapterscore.models import LyricsPreference, RankedTrack, SearchQuerySpec
from chapterscore.spotify.ranking import (
    book_vibe_multiplier,
    is_too_intense_for_reading,
    reading_energy_fit,
    reading_safe_energy_target,
    score_track,
    smooth_playlist_order,
    track_energy_estimate,
)


def _t(
    id_: str,
    name: str,
    artists: list[str],
    *,
    energy: float,
    popularity: int = 55,
    score: float = 50.0,
) -> RankedTrack:
    tr = RankedTrack(
        uri=f"spotify:track:{id_}",
        id=id_,
        name=name,
        artists=artists,
        popularity=popularity,
        duration_ms=210_000,
        features={"energy": energy, "instrumentalness": 0.9, "acousticness": 0.4},
    )
    tr.score = score
    return tr


def test_reading_safe_energy_compresses_high_book_energy():
    low = reading_safe_energy_target(0.25, intimacy_vs_epic=0.7)
    mid = reading_safe_energy_target(0.55, intimacy_vs_epic=0.5)
    high = reading_safe_energy_target(0.95, intimacy_vs_epic=0.2)
    assert low < mid <= high
    # Even a very high-energy book stays in a readable band
    assert high <= 0.56
    assert high < 0.7


def test_intimate_book_caps_target_lower():
    high_intimate = reading_safe_energy_target(0.9, intimacy_vs_epic=0.85)
    high_epic_scale = reading_safe_energy_target(0.9, intimacy_vs_epic=0.2)
    assert high_intimate <= high_epic_scale
    assert high_intimate <= 0.46


def test_rejects_trailer_intensity_for_reading():
    loud = _t("1", "Epic Battle Trailer", ["Two Steps From Hell"], energy=0.92)
    soft = _t("2", "On the Nature of Daylight", ["Max Richter"], energy=0.28)
    assert is_too_intense_for_reading(loud) is True
    assert is_too_intense_for_reading(soft) is False


def test_rejects_high_feature_energy_above_ceiling():
    blasting = _t("3", "Quiet Looking Title", ["Unknown"], energy=0.88)
    assert is_too_intense_for_reading(blasting) is True


def test_overshoot_hurts_more_than_undershoot():
    target = 0.4
    over = reading_energy_fit(0.58, target)
    under = reading_energy_fit(0.22, target)
    assert under > over


def test_atmosphere_beats_trailer_for_intimate_book():
    trailer = _t("t", "War Drums Battle Theme", ["Audiomachine"], energy=0.85, popularity=70)
    piano = _t("p", "Bittersweet Piano", ["Max Richter"], energy=0.3, popularity=65)
    m_trail = book_vibe_multiplier(
        trailer,
        book_energy=0.4,
        atmospheres=["intimate", "melancholic"],
        overall_mood="bittersweet",
        intimacy_vs_epic=0.85,
        setting_texture="contemporary apartments rain-soaked windows",
        era_feel="modern literary",
        anti_generic_notes=["NOT epic battle music"],
    )
    m_piano = book_vibe_multiplier(
        piano,
        book_energy=0.4,
        atmospheres=["intimate", "melancholic"],
        overall_mood="bittersweet",
        intimacy_vs_epic=0.85,
        setting_texture="contemporary apartments rain-soaked windows",
        era_feel="modern literary",
        anti_generic_notes=["NOT epic battle music"],
    )
    assert m_piano > m_trail
    assert m_trail < 0.5


def test_high_energy_book_still_penalizes_trailer_blast():
    """High-energy books can be taut — not Two Steps From Hell."""
    trailer = _t("t2", "Epic Orchestral Trailer", ["Two Steps From Hell"], energy=0.9)
    taut = _t("d", "Driving Tension Underscore", ["Cliff Martinez"], energy=0.52)
    kwargs = dict(
        book_energy=0.85,
        atmospheres=["tense", "dark"],
        overall_mood="propulsive dread",
        intimacy_vs_epic=0.35,
        setting_texture="dystopian city under surveillance",
        era_feel="near-future",
        pacing="propulsive",
    )
    assert book_vibe_multiplier(taut, **kwargs) > book_vibe_multiplier(trailer, **kwargs)


def test_setting_tokens_boost_fit():
    desert = _t("d1", "Deserts Sietch Ambient", ["Unknown"], energy=0.4)
    generic = _t("g1", "Random Pop Hit", ["Pop Star"], energy=0.4)
    kwargs = dict(
        book_energy=0.55,
        atmospheres=["mysterious"],
        overall_mood="arid wonder",
        intimacy_vs_epic=0.4,
        setting_texture="spice deserts imperial courts sietch caverns",
        era_feel="far-future feudal space opera",
    )
    assert book_vibe_multiplier(desert, **kwargs) > book_vibe_multiplier(generic, **kwargs)


def test_smooth_order_limits_adjacent_jumps():
    tracks = [
        _t("a", "Soft A", ["A"], energy=0.2, score=80),
        _t("b", "Blast B", ["B"], energy=0.85, score=90),  # should be dropped or isolated
        _t("c", "Mid C", ["C"], energy=0.35, score=70),
        _t("d", "Mid D", ["D"], energy=0.42, score=75),
        _t("e", "Soft E", ["E"], energy=0.25, score=65),
    ]
    # Mark blast as intense so content filter would drop it; smoothing also drops big jumps
    ordered = smooth_playlist_order(tracks, max_jump=0.20, drop_jarring=True)
    energies = [track_energy_estimate(t) for t in ordered]
    # No adjacent jump larger than ~0.33 (1.65 * 0.20)
    for i in range(1, len(energies)):
        assert abs(energies[i] - energies[i - 1]) <= 0.34
    # Soft cluster should dominate; blast likely dropped
    assert all(e < 0.7 for e in energies) or len(ordered) < len(tracks)


def test_score_uses_reading_safe_target_not_raw_high_energy():
    spec = SearchQuerySpec(query="tense ambient", energy=0.85)
    soft = _t("s", "Tense Ambient Pulse", ["Composer"], energy=0.48, popularity=60)
    loud = _t("l", "Epic Battle Fanfare", ["Two Steps From Hell"], energy=0.92, popularity=80)
    s_soft = score_track(
        soft,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        book_energy=0.85,
        intimacy_vs_epic=0.4,
        atmospheres=["tense"],
        overall_mood="taut dread",
        setting_texture="grey corridors",
    )
    s_loud = score_track(
        loud,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        book_energy=0.85,
        intimacy_vs_epic=0.4,
        atmospheres=["tense"],
        overall_mood="taut dread",
        setting_texture="grey corridors",
    )
    assert s_soft > s_loud
