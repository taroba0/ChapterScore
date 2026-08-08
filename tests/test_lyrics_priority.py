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


def test_taste_not_disabled_under_instrumental_only():
    """Instrumental-only must not hard-block Top Artists."""
    prefs = PersonalizationPrefs(taste_strength=TasteStrength.TOP_10)
    assert prefs.effective_taste(LyricsPreference.INSTRUMENTAL_ONLY) is TasteStrength.TOP_10
    assert prefs.effective_taste(LyricsPreference.ALLOW_LYRICS) is TasteStrength.TOP_10
    assert (
        LyricsPreference.INSTRUMENTAL_ONLY.effective_taste(TasteStrength.TOP_5)
        is TasteStrength.TOP_5
    )


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


def test_intimate_book_penalizes_epic_trailer():
    """Tomorrow-and-Tomorrow vibe should crush Pirates / Two Steps from Hell."""
    from chapterscore.spotify.ranking import book_vibe_multiplier

    epic = _t(
        "He's a Pirate",
        ["Klaus Badelt", "Hans Zimmer"],
        mq="pirates of the caribbean soundtrack",
        features={"energy": 0.85, "instrumentalness": 0.9},
    )
    intimate = _t(
        "On the Nature of Daylight",
        ["Max Richter"],
        mq="melancholic piano",
        features={"energy": 0.25, "instrumentalness": 0.95},
    )
    atms = ["nostalgic", "intimate", "melancholic", "hopeful", "playful"]
    m_epic = book_vibe_multiplier(
        epic, book_energy=0.4, atmospheres=atms, overall_mood="bittersweet nostalgic"
    )
    m_int = book_vibe_multiplier(
        intimate, book_energy=0.4, atmospheres=atms, overall_mood="bittersweet nostalgic"
    )
    assert m_epic < 0.4
    assert m_int > m_epic

    spec = SearchQuerySpec(query="nostalgic intimate piano", energy=0.4, valence=0.35)
    s_epic = score_track(
        epic,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        book_energy=0.4,
        atmospheres=atms,
        overall_mood="bittersweet",
    )
    s_int = score_track(
        intimate,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        book_energy=0.4,
        atmospheres=atms,
        overall_mood="bittersweet",
    )
    assert s_int > s_epic
