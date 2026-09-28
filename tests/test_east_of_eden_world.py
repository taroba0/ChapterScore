"""East of Eden / realist literary world-fit: no Potter, F1, or generic cinema."""

from chapterscore.models import BookVibeAnalysis, LyricsPreference, RankedTrack, SearchQuerySpec
from chapterscore.spotify.queries import (
    allows_generic_cinematic_fallback,
    cinematic_fallback_queries,
    expand_queries_from_analysis,
    is_realist_literary_world,
    vibe_instrumental_queries,
    world_locked_queries,
)
from chapterscore.spotify.ranking import (
    is_world_style_mismatch,
    style_clash_near_kill,
    style_clash_score,
)


def _east_of_eden() -> BookVibeAnalysis:
    return BookVibeAnalysis(
        book_title="East of Eden",
        authors=["John Steinbeck"],
        overall_mood="dusty moral melancholy",
        overall_energy=0.42,
        atmospheres=["melancholic", "earthy", "solemn", "intimate"],
        tone="moral and earthy",
        narrative_voice="omniscient, biblical cadence, grounded",
        writing_style="classic realist literary prose",
        dominant_tones=["melancholic", "moral", "earthy"],
        secondary_tones=["tender", "bitter"],
        humor_level=0.2,
        intimacy_vs_epic=0.72,
        realism_vs_dreaminess=0.2,
        era_feel="late 1800s early 1900s rural California",
        setting_texture="Salinas Valley farms, dusty small-town streets, family ranches",
        sensory_atmosphere="dry heat, tilled earth, quiet kitchens, orchard dust",
        distinctive_signature="Steinbeck family saga of good and evil in pastoral California",
        genre_peers_contrast="realist literary, not mythic fantasy or adventure epic",
        anti_generic_notes=[
            "NOT epic trailer music",
            "NOT magical fantasy adventure",
            "NOT sci-fi or futuristic",
            "NOT modern racing spectacle",
        ],
        key_themes=["family", "good and evil", "inheritance", "land"],
        suitable_styles=[
            "pastoral americana",
            "sparse piano strings",
            "chamber folk",
            "quiet documentary score",
            "dusty small-town melancholy",
        ],
        avoid_styles=[
            "magical",
            "epic trailer",
            "sci-fi",
            "racing",
            "modern spectacle",
            "fantasy adventure",
            "battle",
        ],
        overall_search_queries=[
            SearchQuerySpec(query="salinas valley pastoral melancholy", energy=0.4, reason="llm"),
            SearchQuerySpec(query="dusty farm acoustic instrumental", energy=0.38, reason="llm"),
        ],
        pacing_profile="slow, deliberate, rural",
    )


def test_east_of_eden_detected_as_realist_world():
    a = _east_of_eden()
    assert is_realist_literary_world(a)
    assert not allows_generic_cinematic_fallback(a)


def test_world_locked_queries_are_pastoral():
    specs = world_locked_queries(_east_of_eden(), LyricsPreference.INSTRUMENTAL_ONLY)
    texts = " ".join(s.query.lower() for s in specs)
    assert any(
        k in texts
        for k in ("pastoral", "americana", "rural", "dusty", "chamber", "salinas", "countryside")
    )
    assert "harry potter" not in texts
    assert "formula 1" not in texts
    assert "john williams" not in texts
    assert "two steps from hell" not in texts


def test_expand_skips_spectacle_for_east_of_eden():
    specs = expand_queries_from_analysis(
        _east_of_eden(), LyricsPreference.INSTRUMENTAL_ONLY, max_queries=40
    )
    texts = [s.query.lower() for s in specs]
    blob = " ".join(texts)
    assert any("pastoral" in t or "americana" in t or "dusty" in t or "salinas" in t for t in texts)
    for bad in (
        "harry potter",
        "hogwarts",
        "formula 1",
        "f1 ",
        "sci-fi",
        "space opera",
        "two steps from hell",
        "epic battle",
        "hybrid trailer",
        "hans zimmer",
        "john williams",
    ):
        assert bad not in blob, f"unexpected spectacle query containing {bad!r}"


def test_vibe_instrumental_uses_pastoral_bank():
    specs = vibe_instrumental_queries(_east_of_eden(), max_queries=24)
    blob = " ".join(s.query.lower() for s in specs)
    assert "pastoral" in blob or "americana" in blob or "rachel portman" in blob
    assert "two steps from hell" not in blob
    assert "hans zimmer" not in blob


def test_cinematic_fallback_redirects_to_pastoral():
    specs = cinematic_fallback_queries(_east_of_eden(), LyricsPreference.INSTRUMENTAL_ONLY)
    assert specs
    blob = " ".join(s.query.lower() for s in specs)
    assert any(k in blob for k in ("pastoral", "americana", "dust", "salinas", "sparse", "chamber"))
    assert "cinematic orchestral soundtrack" not in blob
    assert "harry potter" not in blob
    assert all(s.reason == "pastoral-fallback" for s in specs)


def test_rejects_harry_potter_and_f1_tracks():
    a = _east_of_eden()
    potter = RankedTrack(
        uri="spotify:track:p1",
        id="p1",
        name="Hedwig's Theme",
        artists=["John Williams"],
        album="Harry Potter and the Sorcerer's Stone Soundtrack",
        popularity=80,
        duration_ms=300000,
        matched_query="cinematic orchestral soundtrack",
    )
    f1 = RankedTrack(
        uri="spotify:track:f1",
        id="f1",
        name="F1 Theme",
        artists=["Hans Zimmer"],
        album="Formula 1 The Official Soundtrack",
        popularity=75,
        duration_ms=200000,
        matched_query="epic orchestral",
    )
    pastoral = RankedTrack(
        uri="spotify:track:ok1",
        id="ok1",
        name="Any Other Name",
        artists=["Thomas Newman"],
        album="American Beauty Soundtrack",
        popularity=60,
        duration_ms=240000,
        matched_query="pastoral americana instrumental",
    )
    avoid = a.style_keywords_bad()
    suitable = a.style_keywords_good()

    assert is_world_style_mismatch(
        potter,
        intimacy_vs_epic=a.intimacy_vs_epic,
        realism_vs_dreaminess=a.realism_vs_dreaminess,
        era_feel=a.era_feel,
        setting_texture=a.setting_texture,
        avoid_styles=avoid,
        anti_generic_notes=a.anti_generic_notes,
        overall_mood=a.overall_mood,
        suitable_styles=suitable,
        book_title=a.book_title,
        authors=a.authors,
    )
    assert is_world_style_mismatch(
        f1,
        intimacy_vs_epic=a.intimacy_vs_epic,
        realism_vs_dreaminess=a.realism_vs_dreaminess,
        era_feel=a.era_feel,
        setting_texture=a.setting_texture,
        avoid_styles=avoid,
        anti_generic_notes=a.anti_generic_notes,
        overall_mood=a.overall_mood,
        suitable_styles=suitable,
        book_title=a.book_title,
        authors=a.authors,
    )
    assert not is_world_style_mismatch(
        pastoral,
        intimacy_vs_epic=a.intimacy_vs_epic,
        realism_vs_dreaminess=a.realism_vs_dreaminess,
        era_feel=a.era_feel,
        setting_texture=a.setting_texture,
        avoid_styles=avoid,
        anti_generic_notes=a.anti_generic_notes,
        overall_mood=a.overall_mood,
        suitable_styles=suitable,
        book_title=a.book_title,
        authors=a.authors,
    )

    assert style_clash_near_kill(potter, suitable=suitable, avoid=avoid)
    assert style_clash_near_kill(f1, suitable=suitable, avoid=avoid)
    assert style_clash_score(potter, suitable=suitable, avoid=avoid) < 0.15
    assert style_clash_score(pastoral, suitable=suitable, avoid=avoid) >= 0.9


def test_dune_still_allows_cinematic_when_epic():
    dune = BookVibeAnalysis(
        book_title="Dune",
        authors=["Frank Herbert"],
        overall_mood="prophetic desert grandeur",
        overall_energy=0.78,
        atmospheres=["epic", "mysterious", "tense"],
        intimacy_vs_epic=0.15,
        realism_vs_dreaminess=0.45,
        era_feel="far-future feudal space opera",
        setting_texture="spice deserts, imperial courts",
        suitable_styles=["hybrid orchestral", "desert ambient"],
        avoid_styles=["bubblegum pop"],
    )
    assert not is_realist_literary_world(dune)
    assert allows_generic_cinematic_fallback(dune)
