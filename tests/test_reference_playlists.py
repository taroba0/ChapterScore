"""Reference playlist pool selection by book vibe."""

from chapterscore.models import BookVibeAnalysis, LyricsPreference, SearchQuerySpec
from chapterscore.spotify.reference_playlists import (
    REFERENCE_PLAYLIST_POOLS,
    playlist_search_queries_for_pools,
    reference_playlist_queries,
    reference_pools_for_analysis,
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
        intimacy_vs_epic=0.72,
        realism_vs_dreaminess=0.2,
        era_feel="late 1800s early 1900s rural California",
        setting_texture="Salinas Valley farms, dusty small-town streets, family ranches",
        sensory_atmosphere="dry heat, tilled earth, quiet kitchens, orchard dust",
        distinctive_signature="Steinbeck family saga of good and evil in pastoral California",
        genre_peers_contrast="realist literary, not mythic fantasy or adventure epic",
        anti_generic_notes=["NOT epic trailer music", "NOT magical fantasy adventure"],
        key_themes=["family", "good and evil", "inheritance", "land"],
        suitable_styles=[
            "pastoral americana",
            "sparse piano strings",
            "chamber folk",
            "quiet documentary score",
        ],
        avoid_styles=["magical", "epic trailer", "sci-fi", "fantasy adventure"],
    )


def _the_deal() -> BookVibeAnalysis:
    return BookVibeAnalysis(
        book_title="The Deal",
        authors=["Elle Kennedy"],
        overall_mood="flirty campus romance",
        overall_energy=0.48,
        atmospheres=["romantic", "playful", "intimate", "hopeful"],
        tone="witty contemporary romance",
        narrative_voice="first-person confessional, flirty",
        writing_style="dialogue-heavy contemporary NA",
        dominant_tones=["romantic", "playful", "tender"],
        secondary_tones=["witty"],
        humor_level=0.55,
        intimacy_vs_epic=0.78,
        realism_vs_dreaminess=0.35,
        era_feel="contemporary campus",
        setting_texture="college dorms, hockey rink, campus coffee shops, parties",
        sensory_atmosphere="locker rooms, late-night study sessions, soft pop in cafes",
        distinctive_signature="enemies-to-lovers hockey campus romance",
        genre_peers_contrast="new adult romance, not epic fantasy or literary pastoral",
        anti_generic_notes=["NOT epic trailer", "NOT pastoral americana", "NOT battle score"],
        key_themes=["romance", "college", "trust", "sports"],
        suitable_styles=["soft pop", "chill r&b", "indie folk", "cafe acoustic"],
        suggested_genres=["pop", "r&b", "indie"],
        avoid_styles=["epic trailer", "metal", "horror ambient"],
    )


def _strength_of_the_few() -> BookVibeAnalysis:
    return BookVibeAnalysis(
        book_title="The Strength of the Few",
        authors=["James Islington"],
        overall_mood="mythic epic tension",
        overall_energy=0.62,
        atmospheres=["epic", "mysterious", "tense", "sweeping"],
        tone="grand mythic fantasy",
        intimacy_vs_epic=0.28,
        realism_vs_dreaminess=0.7,
        era_feel="epic fantasy secondary world",
        setting_texture="ancient academies, fractured realities, magical war",
        distinctive_signature="multi-world epic fantasy intrigue",
        genre_peers_contrast="high fantasy, not campus romance",
        anti_generic_notes=[],
        key_themes=["power", "fate", "war", "magic"],
        suitable_styles=["orchestral fantasy score", "ambient tension", "cinematic strings"],
        avoid_styles=["bubblegum pop", "country"],
    )


def test_pools_dict_has_soft_pop_and_cafe():
    assert "cafe_reads" in REFERENCE_PLAYLIST_POOLS
    assert "soft_pop" in REFERENCE_PLAYLIST_POOLS
    assert "pastoral_rural" in REFERENCE_PLAYLIST_POOLS
    assert len(REFERENCE_PLAYLIST_POOLS["soft_pop"]) >= 3


def test_romance_pools_lean_cafe_soft_pop_rnb():
    pools = reference_pools_for_analysis(_the_deal(), LyricsPreference.ALLOW_LYRICS)
    assert "cafe_reads" in pools or "soft_pop" in pools
    assert "rnb_soul" in pools or "soft_pop" in pools or "folk_indie" in pools
    # Should not be dominated by pastoral/soundtrack spectacle
    assert "pastoral_rural" not in pools[:3]
    assert pools[0] in {"cafe_reads", "soft_pop", "rnb_soul", "folk_indie"}


def test_pastoral_pools_lean_guitar_piano_chamber():
    pools = reference_pools_for_analysis(
        _east_of_eden(), LyricsPreference.INSTRUMENTAL_ONLY
    )
    assert "pastoral_rural" in pools or "guitar" in pools
    assert "piano" in pools or "strings_chamber" in pools
    assert "soft_pop" not in pools[:2]
    assert "rnb_soul" not in pools[:3]


def test_epic_fantasy_leans_soundtrack_ambient():
    pools = reference_pools_for_analysis(
        _strength_of_the_few(), LyricsPreference.INSTRUMENTAL_ONLY
    )
    assert "soundtrack" in pools or "ambient_focus" in pools
    assert "rnb_soul" not in pools[:3]
    assert "soft_pop" not in pools[:3]


def test_romance_and_pastoral_pools_diverge():
    romance = set(reference_pools_for_analysis(_the_deal(), LyricsPreference.ALLOW_LYRICS))
    pastoral = set(
        reference_pools_for_analysis(_east_of_eden(), LyricsPreference.INSTRUMENTAL_ONLY)
    )
    # Overlap of a few texture pools is OK; top families should differ
    assert romance != pastoral
    overlap = romance & pastoral
    assert len(overlap) <= 4


def test_instrumental_only_prefers_instrumental_queries():
    pairs = reference_playlist_queries(
        _east_of_eden(), LyricsPreference.INSTRUMENTAL_ONLY, max_queries=10
    )
    assert pairs
    texts = " ".join(q for _, q in pairs).lower()
    assert any(
        k in texts
        for k in ("instrumental", "piano", "ambient", "guitar", "score", "chamber")
    )
    # Vocal-heavy romance soft-pop phrases should not dominate instrumental pastoral
    assert "campus soft pop" not in texts


def test_instrumental_pool_query_helper_rewrites_vocal_families():
    qs = playlist_search_queries_for_pools(["rnb_soul", "soft_pop"], instrumental=True, max_queries=8)
    joined = " ".join(qs).lower()
    assert "instrumental" in joined


def test_romance_query_pairs_include_cafe_or_soft_pop():
    pairs = reference_playlist_queries(
        _the_deal(), LyricsPreference.ALLOW_LYRICS, max_queries=12
    )
    pools = {p for p, _ in pairs}
    assert pools & {"cafe_reads", "soft_pop", "rnb_soul", "folk_indie"}
