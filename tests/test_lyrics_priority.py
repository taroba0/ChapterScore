"""Hard priority: instrumental filter + style clash + taste disable."""

from chapterscore.models import (
    LyricsPreference,
    PersonalizationPrefs,
    RankedTrack,
    SearchQuerySpec,
    TasteStrength,
)
from chapterscore.spotify.ranking import (
    InstrumentalStrictness,
    passes_lyrics_filter,
    score_track,
    style_clash_score,
)


def _t(name, artists, **kw):
    return RankedTrack(
        uri="spotify:track:1",
        id="1",
        name=name,
        artists=artists,
        popularity=kw.get("pop", 50),
        duration_ms=200_000,
        features=kw.get("features", {}),
        matched_query=kw.get("mq", "cinematic score"),
    )


def test_instrumental_only_rejects_clear_vocals():
    vocal = _t("Love Song", ["Singer"], features={"instrumentalness": 0.1, "speechiness": 0.2})
    assert passes_lyrics_filter(vocal, LyricsPreference.INSTRUMENTAL_ONLY) is False


def test_instrumental_only_rejects_lyrics_in_title():
    t = _t("Theme (With Lyrics)", ["Composer"], features={"instrumentalness": 0.9})
    assert passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY) is False


def test_instrumental_only_accepts_score():
    t = _t(
        "Main Theme",
        ["Hans Zimmer"],
        features={"instrumentalness": 0.92, "speechiness": 0.02},
        mq="epic orchestral score",
    )
    assert (
        passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY, strictness=InstrumentalStrictness.STRICT)
        is True
    )


def test_instrumental_only_rejects_mid_instrumentalness():
    t = _t(
        "Maybe Vocal",
        ["Band"],
        features={"instrumentalness": 0.45, "speechiness": 0.05},
        mq="cinematic soundtrack",
    )
    assert passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY) is False


def test_query_alone_cannot_admit_random_track():
    t = _t("Hello", ["Adele"], features={}, mq="cinematic orchestral soundtrack instrumental")
    assert passes_lyrics_filter(t, LyricsPreference.INSTRUMENTAL_ONLY) is False


def test_allow_lyrics_accepts_vocals():
    vocal = _t("Love Song", ["Singer"], features={"instrumentalness": 0.05})
    assert passes_lyrics_filter(vocal, LyricsPreference.ALLOW_LYRICS) is True


def test_taste_disabled_under_instrumental_only():
    prefs = PersonalizationPrefs(taste_strength=TasteStrength.TOP_10)
    assert prefs.effective_taste(LyricsPreference.INSTRUMENTAL_ONLY) is TasteStrength.DISABLE
    assert prefs.effective_taste(LyricsPreference.ALLOW_LYRICS) is TasteStrength.TOP_10


def test_style_clash_penalizes_country_in_dystopia():
    country = _t("Dusty Road", ["Country Star"], mq="country ballad")
    ambient = _t("Grey Sprawl", ["Dark Ambient"], mq="dark ambient dystopia")
    avoid = ["country", "bubblegum pop"]
    suitable = ["dark ambient", "industrial", "orchestral"]
    assert style_clash_score(country, suitable=suitable, avoid=avoid) < 0.5
    assert style_clash_score(ambient, suitable=suitable, avoid=avoid) >= 1.0


def test_style_beats_taste_affinity():
    """Book style clash should outweigh personal taste boost."""
    spec = SearchQuerySpec(query="dark dystopia", energy=0.5)
    clash = _t("Honky Tonk Night", ["My Favorite Country Artist"], pop=80)
    fit = _t("Neon Rain", ["Unknown Ambient"], pop=40)
    s_clash = score_track(
        clash,
        spec,
        LyricsPreference.ALLOW_LYRICS,
        taste_affinity=1.0,
        exploration=10,
        avoid_styles=["country"],
        suitable_styles=["dark ambient"],
    )
    s_fit = score_track(
        fit,
        spec,
        LyricsPreference.ALLOW_LYRICS,
        taste_affinity=0.0,
        exploration=10,
        avoid_styles=["country"],
        suitable_styles=["dark ambient", "ambient"],
    )
    assert s_fit > s_clash
