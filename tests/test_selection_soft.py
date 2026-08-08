"""Soft length targets, overall cohesion, and de-dupe helpers."""

from chapterscore.models import LyricsPreference, RankedTrack
from chapterscore.spotify.selection import (
    _pick_quality,
    _quality_floor,
    _should_search_more,
    _target_count,
)


def _t(id_: str, score: float, energy: float = 0.4, name: str | None = None) -> RankedTrack:
    return RankedTrack(
        uri=f"spotify:track:{id_}",
        id=id_,
        name=name or f"Track {id_}",
        artists=["Artist"],
        popularity=50,
        duration_ms=210_000,
        score=score,
        features={"energy": energy, "instrumentalness": 0.9},
    )


def test_soft_target_stops_before_full_count():
    """Having ~60% good tracks should stop further searching."""
    target = 20
    good = [_t(f"g{i}", score=40.0) for i in range(12)]
    assert _should_search_more(good, target, min_hours=None, quality_floor=28.0) is False


def test_soft_target_continues_when_thin():
    thin = [_t("a", score=40.0), _t("b", score=35.0)]
    assert _should_search_more(thin, 20, min_hours=None, quality_floor=28.0) is True


def test_pick_quality_does_not_pad_with_weak_tracks():
    pool = [_t(f"s{i}", score=45.0) for i in range(5)] + [
        _t(f"w{i}", score=5.0, name=f"Weak {i}") for i in range(20)
    ]
    # Distinct artists so artist-cap doesn't hide weak tracks
    for i, t in enumerate(pool):
        t.artists = [f"Artist {i}"]
    chosen = _pick_quality(
        pool,
        target=20,
        max_per_artist=3,
        lyrics=LyricsPreference.INSTRUMENTAL_ONLY,
        book_energy=0.4,
        cohesive=True,
    )
    assert len(chosen) == 5  # only the strong ones
    assert all(t.score >= 28.0 for t in chosen)
    assert len({t.id for t in chosen}) == len(chosen)


def test_pick_quality_dedupes():
    a = _t("1", score=50.0, name="Theme")
    b = _t("2", score=48.0, name="Theme - Remastered")
    a.artists = b.artists = ["Same Composer"]
    c = _t("3", score=47.0, name="Other Cue")
    c.artists = ["Same Composer"]
    chosen = _pick_quality(
        [a, b, c],
        target=10,
        max_per_artist=5,
        lyrics=LyricsPreference.INSTRUMENTAL_ONLY,
        cohesive=False,
    )
    assert len(chosen) == 2


def test_target_count_from_hours_is_soft_aim_only():
    n = _target_count(tracks_requested=10, min_tracks=12, min_hours=1.5)
    assert n >= 12
    assert _quality_floor(LyricsPreference.INSTRUMENTAL_ONLY) >= 20
