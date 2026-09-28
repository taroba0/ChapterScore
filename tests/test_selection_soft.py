"""Duration targets, overall cohesion, and de-dupe helpers."""

from chapterscore.models import LyricsPreference, RankedTrack
from chapterscore.spotify.selection import (
    _DURATION_FILL_RATIO,
    _pick_quality,
    _playlist_hours,
    _quality_floor,
    _should_search_more,
    _target_count,
    format_duration_report,
)


def _t(
    id_: str,
    score: float,
    energy: float = 0.4,
    name: str | None = None,
    duration_ms: int = 210_000,
) -> RankedTrack:
    return RankedTrack(
        uri=f"spotify:track:{id_}",
        id=id_,
        name=name or f"Track {id_}",
        artists=["Artist"],
        popularity=50,
        duration_ms=duration_ms,
        score=score,
        features={"energy": energy, "instrumentalness": 0.9},
    )


def test_count_only_stops_near_seventy_percent():
    """Without min_hours, ~70% good tracks is enough to stop."""
    target = 20
    good = [_t(f"g{i}", score=40.0) for i in range(14)]
    assert _should_search_more(good, target, min_hours=None, quality_floor=28.0) is False


def test_count_only_continues_when_thin():
    thin = [_t("a", score=40.0), _t("b", score=35.0)]
    assert _should_search_more(thin, 20, min_hours=None, quality_floor=28.0) is True


def test_min_hours_keeps_searching_until_near_target():
    """3h request must not stop at ~55 minutes (~0.3 of target)."""
    # 15 tracks × 3.5 min ≈ 52.5 min ≈ 0.875 h — far under 3h
    short = [_t(f"s{i}", score=40.0, duration_ms=210_000) for i in range(15)]
    assert _playlist_hours(short) < 1.0
    assert _should_search_more(short, target=52, min_hours=3.0, quality_floor=28.0) is True

    # Still under 88% of 3h → keep searching
    # 45 tracks × 3.5 min = 157.5 min = 2.625 h < 3*0.88=2.64
    mid = [_t(f"m{i}", score=40.0, duration_ms=210_000) for i in range(45)]
    assert _playlist_hours(mid) < 3.0 * _DURATION_FILL_RATIO
    assert _should_search_more(mid, target=52, min_hours=3.0, quality_floor=28.0) is True

    # ~2.7h+ with enough tracks → can stop
    # 50 tracks × 3.5 min = 175 min = 2.917 h > 2.64
    near = [_t(f"n{i}", score=40.0, duration_ms=210_000) for i in range(50)]
    assert _playlist_hours(near) >= 3.0 * _DURATION_FILL_RATIO
    assert _should_search_more(near, target=52, min_hours=3.0, quality_floor=28.0) is False


def test_duration_report_shows_requested_vs_actual():
    tracks = [_t(f"t{i}", score=40.0, duration_ms=210_000) for i in range(12)]
    report = format_duration_report(tracks, 3.0)
    assert "Requested" in report
    assert "3" in report
    assert "Actual" in report
    assert "under target" in report.lower() or "%" in report


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


def test_target_count_from_hours_scales_for_long_sessions():
    n = _target_count(tracks_requested=10, min_tracks=12, min_hours=3.0)
    # ~3h / 3.5min ≈ 51+ tracks
    assert n >= 50
    assert n <= 160
    assert _quality_floor(LyricsPreference.INSTRUMENTAL_ONLY) >= 20
