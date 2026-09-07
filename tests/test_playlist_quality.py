"""Composition de-dupe, dead-track rejection, and popularity quality floor."""

from chapterscore.models import LyricsPreference, RankedTrack
from chapterscore.spotify.ranking import (
    composition_key,
    dedupe_tracks,
    is_dead_or_exercise_track,
    normalize_composition_title,
    passes_content_filter,
    passes_lyrics_filter,
    select_diverse,
)


def _t(
    id_: str,
    name: str,
    artists: list[str] | None = None,
    *,
    popularity: int = 50,
    score: float = 50.0,
    album: str = "",
    features: dict | None = None,
    duration_ms: int = 200_000,
) -> RankedTrack:
    tr = RankedTrack(
        uri=f"spotify:track:{id_}",
        id=id_,
        name=name,
        artists=artists or ["Artist"],
        album=album,
        popularity=popularity,
        duration_ms=duration_ms,
        features=features or {},
    )
    tr.score = score
    return tr


def test_normalize_strips_soundtrack_and_remaster_suffixes():
    a = normalize_composition_title("Cornfield Chase - From Interstellar")
    b = normalize_composition_title("Cornfield Chase (Remastered 2018)")
    c = normalize_composition_title("Cornfield Chase (Instrumental)")
    assert a == b == c == "cornfieldchase"


def test_composition_key_merges_cross_artist_same_work():
    t1 = _t("1", "He's a Pirate", ["Klaus Badelt"], popularity=70, score=80)
    t2 = _t("2", "He's a Pirate (From Pirates of the Caribbean)", ["Hans Zimmer"], popularity=60, score=70)
    assert composition_key(t1) == composition_key(t2)


def test_generic_title_still_keys_on_artist():
    t1 = _t("1", "Time", ["Hans Zimmer"])
    t2 = _t("2", "Time", ["Pink Floyd"])
    assert composition_key(t1) != composition_key(t2)


def test_dedupe_keeps_best_score_popularity():
    weak = _t("a", "Main Title Theme (Live)", ["Cover Band"], popularity=20, score=40)
    strong = _t("b", "Main Title Theme", ["Howard Shore"], popularity=75, score=70)
    out = dedupe_tracks([weak, strong])
    assert len(out) == 1
    assert out[0].id == "b"


def test_rejects_g_minor_key_only():
    t = _t("g1", "G minor", ["Studio Orchestra"], features={"energy": 0.05, "valence": 0.05})
    assert is_dead_or_exercise_track(t) is True
    assert passes_content_filter(t) is False


def test_rejects_scale_and_drone_titles():
    assert is_dead_or_exercise_track(_t("1", "Chromatic Scale Practice")) is True
    assert is_dead_or_exercise_track(_t("2", "Ambient Drone")) is True
    assert is_dead_or_exercise_track(_t("3", "A Major Scale")) is True
    assert is_dead_or_exercise_track(_t("4", "Orchestra Tuning")) is True


def test_keeps_real_piece_in_a_key():
    """Artistic titles that merely mention a key should survive."""
    t = _t(
        "s1",
        "Piano Sonata in C-sharp minor",
        ["Beethoven"],
        popularity=60,
        features={"energy": 0.3, "valence": 0.25},
    )
    assert is_dead_or_exercise_track(t) is False
    assert passes_content_filter(t) is True


def test_keeps_real_soundtrack_cue():
    t = _t(
        "c1",
        "Cornfield Chase",
        ["Hans Zimmer"],
        popularity=65,
        features={"energy": 0.4, "instrumentalness": 0.95},
    )
    assert passes_content_filter(t) is True
    assert passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY) is True


def test_select_diverse_no_duplicate_compositions():
    tracks = [
        _t("1", "Arrival", ["Max Richter"], popularity=80, score=90),
        _t("2", "Arrival (Remastered)", ["Max Richter"], popularity=50, score=60),
        _t("3", "Arrival - From the Soundtrack", ["Cover Ensemble"], popularity=30, score=40),
        _t("4", "On the Nature of Daylight", ["Max Richter"], popularity=70, score=85),
    ]
    chosen = select_diverse(tracks, n=10, max_per_artist=5)
    titles = {normalize_composition_title(t.name) for t in chosen}
    assert "arrival" in titles
    assert len([t for t in chosen if normalize_composition_title(t.name) == "arrival"]) == 1
    assert any("daylight" in normalize_composition_title(t.name) for t in chosen)
