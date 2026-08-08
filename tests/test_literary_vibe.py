"""Tests for multi-dimensional literary vibe models and anti-generic differentiation."""

from chapterscore.models import (
    BookMetadata,
    BookVibeAnalysis,
    EmotionalAct,
    LyricsPreference,
    RankedTrack,
    SearchQuerySpec,
)
from chapterscore.spotify.queries import (
    expand_queries_from_analysis,
    vibe_instrumental_queries,
    _book_energy_band,
)
from chapterscore.spotify.ranking import book_vibe_multiplier, score_track


def _intimate_literary() -> BookVibeAnalysis:
    """Sally Rooney–ish: intimate, wry, contemporary, low epic."""
    return BookVibeAnalysis(
        book_title="Normal People",
        authors=["Sally Rooney"],
        overall_mood="bittersweet intimacy",
        overall_energy=0.38,
        atmospheres=["intimate", "melancholic", "hopeful", "nostalgic"],
        emotional_arc="awkward closeness → rupture → tentative reconnection",
        tone="wry and tender",
        narrative_voice="intimate third-person, understated irony",
        writing_style="spare contemporary dialogue-forward prose",
        dominant_tones=["bittersweet", "anxious", "tender"],
        secondary_tones=["wry", "hopeful"],
        humor_level=0.45,
        sarcasm_irony_level=0.55,
        intimacy_vs_epic=0.88,
        realism_vs_dreaminess=0.25,
        era_feel="contemporary Ireland",
        setting_texture="small-town classrooms, Dublin flats, quiet kitchens",
        sensory_atmosphere="rain on windows, low light, held breath between texts",
        distinctive_signature=(
            "class-anxious millennial romance told in spare, almost clinical intimacy"
        ),
        genre_peers_contrast="less melodramatic than typical YA romance; more ironic",
        anti_generic_notes=[
            "NOT epic battle music",
            "NOT sweeping trailer orchestra",
            "NOT dark dystopian industrial",
        ],
        key_themes=["class", "intimacy", "miscommunication", "desire"],
        suitable_styles=[
            "bittersweet neoclassical piano",
            "intimate indie folk",
            "quiet ambient",
            "lo-fi nostalgic",
        ],
        avoid_styles=["country", "epic trailer", "metal", "bubblegum pop"],
        overall_search_queries=[
            SearchQuerySpec(query="bittersweet piano intimacy", energy=0.35, reason="llm"),
            SearchQuerySpec(query="wry tender indie instrumental", energy=0.4, reason="llm"),
        ],
        emotional_acts=[
            EmotionalAct(
                act_id=1,
                label="School years",
                mood="awkward tenderness",
                energy_level=0.35,
                atmospheres=["intimate", "anxious"],
            ),
            EmotionalAct(
                act_id=2,
                label="Dublin distance",
                mood="lonely yearning",
                energy_level=0.4,
                atmospheres=["melancholic", "nostalgic"],
            ),
        ],
    )


def _epic_dystopia() -> BookVibeAnalysis:
    """Dune-ish: epic, prophetic, desert empire — same broad 'genre' bucket as 1984 is not."""
    return BookVibeAnalysis(
        book_title="Dune",
        authors=["Frank Herbert"],
        overall_mood="prophetic desert grandeur",
        overall_energy=0.78,
        atmospheres=["epic", "mysterious", "tense", "solemn"],
        emotional_arc="exile → desert trial → messianic rise",
        tone="solemn and prophetic",
        narrative_voice="omniscient, mythic, multi-perspective",
        writing_style="dense worldbuilding with philosophical asides",
        dominant_tones=["epic", "solemn", "tense"],
        secondary_tones=["mysterious", "triumphant"],
        humor_level=0.1,
        sarcasm_irony_level=0.15,
        intimacy_vs_epic=0.15,
        realism_vs_dreaminess=0.45,
        era_feel="far-future feudal space opera",
        setting_texture="spice deserts, imperial courts, sietch caverns",
        sensory_atmosphere="heat shimmer, sandstorm roar, ritual silence",
        distinctive_signature=(
            "ecological messianism in a feudal space-opera desert"
        ),
        genre_peers_contrast="more ecological and religious than pure military SF",
        anti_generic_notes=["NOT bubblegum pop", "NOT intimate bedroom indie"],
        key_themes=["power", "ecology", "destiny", "religion"],
        suitable_styles=[
            "hybrid orchestral electronic",
            "desert ambient",
            "epic film score",
            "ritual choral",
        ],
        avoid_styles=["country", "bubblegum pop", "light acoustic folk"],
        overall_search_queries=[
            SearchQuerySpec(query="desert empire intrigue", energy=0.7, reason="llm"),
            SearchQuerySpec(query="sandstorm tension underscore", energy=0.8, reason="llm"),
        ],
    )


def _intimate_dystopia() -> BookVibeAnalysis:
    """1984-ish: dystopian but intimate, clinical, paranoid — not epic trailer."""
    return BookVibeAnalysis(
        book_title="1984",
        authors=["George Orwell"],
        overall_mood="claustrophobic dread",
        overall_energy=0.48,
        atmospheres=["tense", "dark", "melancholic", "eerie"],
        emotional_arc="compliance → illicit hope → total crushing",
        tone="bleak and clinical",
        narrative_voice="detached third-person, clinical observation",
        writing_style="plain, precise, oppressive clarity",
        dominant_tones=["bleak", "paranoid", "despairing"],
        secondary_tones=["tender", "bitter"],
        humor_level=0.08,
        sarcasm_irony_level=0.35,
        intimacy_vs_epic=0.72,
        realism_vs_dreaminess=0.15,
        era_feel="mid-century totalitarian London",
        setting_texture="grey ministries, dingy rooms, telescreen glare",
        sensory_atmosphere="dust, boiled cabbage, cold corridors, electric light",
        distinctive_signature=(
            "intimate psychological destruction under bureaucratic totalitarianism"
        ),
        genre_peers_contrast=(
            "not action dystopia — psychological and linguistic oppression, not war"
        ),
        anti_generic_notes=[
            "NOT epic battle music",
            "NOT triumphant trailer orchestra",
            "NOT desert space opera scores",
        ],
        key_themes=["surveillance", "truth", "language", "power"],
        suitable_styles=[
            "cold electronic ambient",
            "tense minimalist score",
            "bleak piano",
            "industrial drone",
        ],
        avoid_styles=["epic trailer", "country", "triumphant brass", "adventure orchestral"],
        overall_search_queries=[
            SearchQuerySpec(query="surveillance dread ambient", energy=0.45, reason="llm"),
            SearchQuerySpec(query="bleak totalitarian piano", energy=0.4, reason="llm"),
        ],
    )


def test_analysis_context_blob_includes_enrichment():
    book = BookMetadata(
        title="X",
        description="Publisher desc",
        plot_summary="Plot happens",
        reception_text="Critics found it wry and devastating",
        themes_text="Themes of memory and class",
        review_snippets=["bittersweet and intimate"],
        subjects=["Literary fiction"],
        genre_labels=["literary fiction"],
    )
    blob = book.analysis_context_blob()
    assert "Publisher" in blob or "description" in blob.lower()
    assert "Plot" in blob or "synopsis" in blob.lower()
    assert "wry" in blob
    assert "memory" in blob
    assert "bittersweet" in blob


def test_vibe_keyword_pool_is_rich():
    a = _intimate_literary()
    pool = " ".join(a.vibe_keyword_pool()).lower()
    assert "wry" in pool or "intimate" in pool
    assert "bittersweet" in pool or "millennial" in pool
    assert a.distinctive_signature


def test_energy_band_differs_by_intimacy():
    assert _book_energy_band(_intimate_literary()) == "intimate"
    assert _book_energy_band(_epic_dystopia()) == "epic"
    # Same-ish energy as mid dystopia but high intimacy → intimate not epic
    assert _book_energy_band(_intimate_dystopia()) == "intimate"


def test_two_dystopias_produce_different_queries():
    """1984 vs Dune must not collapse to the same musical query language."""
    lyrics = LyricsPreference.INSTRUMENTAL_ONLY
    q_orwell = expand_queries_from_analysis(_intimate_dystopia(), lyrics, max_queries=30)
    q_dune = expand_queries_from_analysis(_epic_dystopia(), lyrics, max_queries=30)
    texts_o = " ".join(s.query.lower() for s in q_orwell)
    texts_d = " ".join(s.query.lower() for s in q_dune)

    # Dune should lean epic / desert / space
    assert any(
        k in texts_d for k in ("desert", "epic", "space", "sand", "orchestral", "empire")
    ), texts_d[:400]
    # 1984 should lean cold/bleak/surveillance or intimate piano — not dune desert
    assert any(
        k in texts_o
        for k in (
            "bleak",
            "surveillance",
            "dread",
            "piano",
            "ambient",
            "cold",
            "dystopian",
            "electronic",
        )
    ), texts_o[:400]
    # Anti-generic: intimate dystopia should not be dominated by trailer-epic language
    epic_hits_o = sum(1 for k in ("two steps", "battle", "trailer", "fanfare") if k in texts_o)
    epic_hits_d = sum(
        1 for k in ("epic", "desert", "space", "orchestral", "empire") if k in texts_d
    )
    assert epic_hits_d >= epic_hits_o


def test_intimate_vs_epic_instrumental_banks_differ():
    intimate = vibe_instrumental_queries(_intimate_literary(), max_queries=20)
    epic = vibe_instrumental_queries(_epic_dystopia(), max_queries=20)
    i_text = " ".join(s.query.lower() for s in intimate)
    e_text = " ".join(s.query.lower() for s in epic)
    assert any(k in i_text for k in ("piano", "nils", "richter", "intimate", "neoclassical"))
    assert any(k in e_text for k in ("epic", "zimmer", "trailer", "orchestral", "adventure"))
    # Intimate bank should largely avoid Two Steps / trailer
    assert "two steps from hell" not in i_text


def test_ranking_penalizes_epic_for_intimate_book():
    intimate = _intimate_literary()
    epic_track = RankedTrack(
        uri="spotify:track:epic1",
        id="epic1",
        name="Battle of the Pirates",
        artists=["Two Steps From Hell"],
        album="Epic Trailer Music",
        popularity=70,
        duration_ms=200_000,
        matched_query="epic orchestral",
        features={"energy": 0.9, "instrumentalness": 0.95},
    )
    soft_track = RankedTrack(
        uri="spotify:track:soft1",
        id="soft1",
        name="On the Nature of Daylight",
        artists=["Max Richter"],
        album="The Blue Notebooks",
        popularity=65,
        duration_ms=360_000,
        matched_query="bittersweet piano",
        features={"energy": 0.25, "instrumentalness": 0.98},
    )
    m_epic = book_vibe_multiplier(
        epic_track,
        book_energy=intimate.overall_energy,
        atmospheres=intimate.atmospheres,
        overall_mood=intimate.overall_mood,
        key_themes=intimate.key_themes,
        intimacy_vs_epic=intimate.intimacy_vs_epic,
        narrative_voice=intimate.narrative_voice,
        distinctive_signature=intimate.distinctive_signature,
        anti_generic_notes=intimate.anti_generic_notes,
        humor_level=intimate.humor_level,
    )
    m_soft = book_vibe_multiplier(
        soft_track,
        book_energy=intimate.overall_energy,
        atmospheres=intimate.atmospheres,
        overall_mood=intimate.overall_mood,
        key_themes=intimate.key_themes,
        intimacy_vs_epic=intimate.intimacy_vs_epic,
        narrative_voice=intimate.narrative_voice,
        distinctive_signature=intimate.distinctive_signature,
        anti_generic_notes=intimate.anti_generic_notes,
        humor_level=intimate.humor_level,
    )
    assert m_soft > m_epic
    assert m_epic < 0.5


def test_score_track_uses_intimacy_axis():
    a = _intimate_literary()
    soft = RankedTrack(
        uri="spotify:track:s1",
        id="s1",
        name="Experience",
        artists=["Ludovico Einaudi"],
        album="In a Time Lapse",
        popularity=80,
        duration_ms=300_000,
        matched_query="bittersweet piano intimacy",
        features={"energy": 0.3, "instrumentalness": 0.95, "valence": 0.35},
    )
    spec = SearchQuerySpec(query="bittersweet piano intimacy", energy=0.35)
    score = score_track(
        soft,
        spec,
        LyricsPreference.INSTRUMENTAL_ONLY,
        suitable_styles=a.suitable_styles,
        avoid_styles=a.avoid_styles,
        book_energy=a.overall_energy,
        atmospheres=a.atmospheres,
        overall_mood=a.overall_mood,
        intimacy_vs_epic=a.intimacy_vs_epic,
        narrative_voice=a.narrative_voice,
        distinctive_signature=a.distinctive_signature,
    )
    assert score > 30


def test_emotional_act_roundtrip():
    act = EmotionalAct(
        act_id=2,
        label="Turning point",
        mood="fracture",
        energy_level=0.6,
        atmospheres=["tense", "intimate"],
        vibe_note="The quiet break.",
    )
    restored = EmotionalAct.model_validate(act.model_dump())
    assert restored.label == "Turning point"
    assert restored.energy_level == 0.6


def test_clamp_multi_dim_fields():
    a = BookVibeAnalysis(
        book_title="X",
        overall_mood="test",
        overall_energy=2.0,
        humor_level=-1,
        intimacy_vs_epic=5,
        realism_vs_dreaminess=0.5,
    )
    assert a.overall_energy == 1.0
    assert a.humor_level == 0.0
    assert a.intimacy_vs_epic == 1.0
