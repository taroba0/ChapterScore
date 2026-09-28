"""Expand vibe analysis into a rich, diverse set of Spotify search queries."""

from __future__ import annotations

from chapterscore.models import (
    BookVibeAnalysis,
    ChapterVibe,
    LyricsPreference,
    SearchQuerySpec,
)


def literary_world_blob(analysis: BookVibeAnalysis) -> str:
    """Concatenate literary world signals for world-type detection."""
    parts = [
        analysis.era_feel or "",
        analysis.setting_texture or "",
        analysis.sensory_atmosphere or "",
        analysis.overall_mood or "",
        analysis.tone or "",
        analysis.distinctive_signature or "",
        analysis.genre_peers_contrast or "",
        analysis.writing_style or "",
        analysis.narrative_voice or "",
        analysis.pacing_profile or "",
        analysis.pacing or "",
        " ".join(analysis.atmospheres or []),
        " ".join(analysis.dominant_tones or []),
        " ".join(analysis.secondary_tones or []),
        " ".join(analysis.key_themes or []),
        " ".join(analysis.suitable_styles or []),
        " ".join(analysis.anti_generic_notes or []),
        " ".join(analysis.avoid_styles or []),
        analysis.book_title or "",
    ]
    return " ".join(parts).lower()


def is_realist_literary_world(analysis: BookVibeAnalysis) -> bool:
    """
    Classic realist / historical / rural / small-town literary fiction.

    These books must NOT fall into generic Williams/Potter/Zimmer/racing banks.
    """
    blob = literary_world_blob(analysis)
    intimacy = analysis.intimacy_vs_epic if analysis.intimacy_vs_epic is not None else 0.5
    dream = analysis.realism_vs_dreaminess if analysis.realism_vs_dreaminess is not None else 0.4

    realist_keys = (
        "realist",
        "realism",
        "literary",
        "pastoral",
        "rural",
        "farm",
        "valley",
        "small-town",
        "small town",
        "dusty",
        "earthy",
        "americana",
        "salinas",
        "california",
        "midwest",
        "frontier",
        "homestead",
        "historical",
        "19th",
        "victorian",
        "edwardian",
        "depression-era",
        "dust bowl",
        "moral",
        "melanchol",
        "domestic",
        "everyday",
        "quiet life",
        "steinbeck",
        "family saga",
        "coming-of-age",
    )
    spectacle_keys = (
        "space opera",
        "sci-fi",
        "science fiction",
        "cyber",
        "magic",
        "wizard",
        "hogwarts",
        "mythic epic",
        "superhero",
        "galactic",
        "dystopian war",
        "dragon",
        "quest fantasy",
    )
    hits = sum(1 for k in realist_keys if k in blob)
    spectacle = any(k in blob for k in spectacle_keys)
    # High intimacy + low dreaminess strongly implies grounded literary world
    if intimacy >= 0.55 and dream <= 0.45 and hits >= 1:
        return True
    if hits >= 2 and not spectacle:
        return True
    if hits >= 1 and intimacy >= 0.6 and not spectacle:
        return True
    return False


def allows_generic_cinematic_fallback(analysis: BookVibeAnalysis) -> bool:
    """
    Whether Stage 4–6 generic cinema/composer banks are allowed.

    Blocked for intimate books and realist/historical/rural/literary worlds.
    """
    intimacy = analysis.intimacy_vs_epic if analysis.intimacy_vs_epic is not None else 0.5
    if intimacy >= 0.55:
        return False
    if is_realist_literary_world(analysis):
        return False
    anti = " ".join(analysis.anti_generic_notes or []).lower()
    avoid = " ".join(analysis.avoid_styles or []).lower()
    if any(
        k in anti or k in avoid
        for k in (
            "not epic",
            "no epic",
            "not trailer",
            "not cinematic",
            "not battle",
            "not magical",
            "not fantasy",
            "not sci-fi",
            "not spectacle",
        )
    ):
        return False
    return True


def world_locked_queries(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_queries: int = 14,
) -> list[SearchQuerySpec]:
    """
    Genre-narrow-first queries: lock a small style universe from setting/era/emotion
    before any generic cinema expansion.
    """
    out: list[SearchQuerySpec] = []
    seen: set[str] = set()
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.45
    inst = lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental

    def add(q: str, reason: str) -> None:
        key = " ".join(q.lower().split())
        if not key or key in seen:
            return
        seen.add(key)
        if inst and not any(
            k in key for k in ("instrumental", "score", "soundtrack", "piano", "ambient", "strings")
        ):
            q = f"{q} instrumental"
        out.append(
            SearchQuerySpec(
                query=q,
                energy=energy,
                instrumentalness_min=0.75 if lyrics.normalized().is_instrumental_only else None,
                mood_keywords=list(analysis.atmospheres or [])[:3],
                reason=reason,
            )
        )

    # Setting / era lock
    if analysis.era_feel:
        words = " ".join(analysis.era_feel.split()[:6])
        add(f"{words} pastoral instrumental", reason="world-era")
        add(f"{words} chamber strings", reason="world-era2")
    if analysis.setting_texture:
        words = " ".join(analysis.setting_texture.split()[:6])
        add(f"{words} instrumental", reason="world-setting")
    for tone in (analysis.dominant_tones or [])[:4]:
        add(f"{tone} acoustic instrumental", reason=f"world-tone:{tone}")
    if analysis.overall_mood:
        add(f"{analysis.overall_mood} sparse piano", reason="world-mood")

    for style in (analysis.suitable_styles or [])[:8]:
        s = style.strip()
        if s:
            add(s if "instrumental" in s.lower() else f"{s} instrumental", reason="world-style")

    # Realist / rural family — explicit positive universe
    if is_realist_literary_world(analysis):
        for q in _PASTORAL_REALIST_SEEDS:
            add(q, reason="world-pastoral")
            if len(out) >= max_queries:
                break

    return out[:max_queries]


# Seed banks used when LLM queries are sparse or need broadening.
# Prefer book-world banks; generic cinema is gated separately.
_INSTRUMENTAL_SEEDS = [
    "melancholic piano instrumental",
    "neoclassical piano strings",
    "intimate acoustic instrumental",
    "quiet reflective guitar instrumental",
    "dark ambient atmosphere",
    "tense thriller underscore",
    "post rock instrumental build",
    "hopeful cinematic piano",
    "ominous low brass score",
    "mysterious ambient drone",
    "desert ambient soundscape",
    # Kept but low priority / gated for realist books:
    "cinematic orchestral soundtrack",
    "adventure orchestral theme",
]

_PASTORAL_REALIST_SEEDS = [
    "pastoral americana instrumental",
    "rural folk instrumental melancholic",
    "dusty acoustic guitar instrumental",
    "sparse piano countryside",
    "chamber folk strings quiet",
    "documentary score pastoral",
    "small town melancholy instrumental",
    "earthy acoustic instrumental",
    "thomas newman quiet piano",
    "mark isham pastoral",
    "rachel portman",
    "james newton howard quiet",
    "gustavo santaolalla",
    "nick cave warren ellis instrumental",
    "aaron copland quiet americana",
    "appalachian ambient instrumental",
    "salinas valley mood instrumental",
    "1900s rural america score",
]

# Intimate / emotional instrumental (bittersweet novels, character drama)
_INTIMATE_INSTRUMENTAL = [
    "max richter",
    "nils frahm",
    "olafur arnalds",
    "ludovico einaudi",
    "thomas newman",
    "yann tiersen",
    "yiruma",
    "johann johannsson",
    "hildur gudnadottir",
    "a winged victory for the sullen",
    "dustin ohalloran",
    "library tapes",
    "neoclassical piano intimate",
    "melancholic piano instrumental",
    "bittersweet piano strings",
    "nostalgic ambient piano",
    "intimate chamber strings",
    "emotional film score piano quiet",
    "delicate orchestral score",
    "hopeful piano instrumental",
    "playful pizzicato instrumental",
    "lofi ambient instrumental nostalgic",
    "post rock quiet instrumental",
    "acoustic guitar instrumental melancholic",
]

# Epic / action cinematic — only when the book energy/atmosphere warrants it
_EPIC_CINEMATIC = [
    "hans zimmer",
    "two steps from hell",
    "thomas bergersen",
    "audiomachine",
    "epic orchestral film score",
    "hybrid orchestral trailer",
    "adventure film soundtrack instrumental",
    "triumphant brass fanfare instrumental",
]

# Mid-energy cinematic (drama, mystery) — lighter touch
_DRAMA_CINEMATIC = [
    "thomas newman",
    "alexandre desplat",
    "james newton howard",
    "carter burwell",
    "cliff martinez",
    "emotional orchestral soundtrack",
    "cinematic piano score",
    "subtle film score strings",
]


def _book_energy_band(analysis: BookVibeAnalysis) -> str:
    """
    Map literary scale + energy → instrumental seed bank.

    Prefer intimacy_vs_epic (0=epic, 1=intimate) when present so two books
    with similar energy but different scale get different music.
    """
    e = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    intimacy = getattr(analysis, "intimacy_vs_epic", None)
    if intimacy is None:
        intimacy = 0.5
    atms = {a.lower() for a in (analysis.atmospheres or [])}
    tones = {t.lower() for t in (analysis.dominant_tones or [])} | {
        t.lower() for t in (analysis.secondary_tones or [])
    }
    intimate_keys = {
        "intimate",
        "melancholic",
        "nostalgic",
        "hopeful",
        "playful",
        "romantic",
        "calm",
        "solemn",
        "bittersweet",
        "tender",
        "wry",
        "quiet",
    }
    epic_keys = {"epic", "triumphant", "adventurous", "angry", "tense", "sweeping", "grand"}
    voice = (analysis.narrative_voice or "").lower()
    anti = " ".join(analysis.anti_generic_notes or []).lower()
    blocks_epic = any(
        k in anti for k in ("not epic", "no epic", "not trailer", "no trailer", "not battle")
    )

    # Explicit literary intimacy wins over raw energy
    if intimacy >= 0.65 or blocks_epic:
        if atms & epic_keys and e >= 0.7 and intimacy < 0.55:
            return "drama"
        return "intimate"
    if intimacy <= 0.3 and (e >= 0.6 or atms & epic_keys):
        return "epic"

    if e <= 0.45 or (atms & intimate_keys and e < 0.65) or tones & intimate_keys:
        if atms & epic_keys and e >= 0.55 and intimacy < 0.5:
            return "drama"
        return "intimate"
    if e >= 0.72 or (atms & epic_keys and e >= 0.6 and intimacy < 0.45):
        return "epic"
    if any(k in voice for k in ("intimate", "wry", "earnest", "confessional", "first-person")):
        if e < 0.7:
            return "intimate"
    return "drama"


def vibe_instrumental_queries(
    analysis: BookVibeAnalysis,
    *,
    max_queries: int = 24,
) -> list[SearchQuerySpec]:
    """
    Instrumental-only query bank driven by **book vibe**, not generic epic cinema.

    Intimate/bittersweet books → piano, neoclassical, quiet scores.
    Epic books → only then lean into trailer/Zimmer-style material.
    """
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    mood = (analysis.overall_mood or "reflective").lower()
    band = _book_energy_band(analysis)
    out: list[SearchQuerySpec] = []
    seen: set[str] = set()

    def add(q: str, reason: str = "vibe-inst") -> None:
        key = " ".join(q.lower().split())
        if not key or key in seen:
            return
        seen.add(key)
        out.append(
            SearchQuerySpec(
                query=q,
                energy=energy,
                instrumentalness_min=0.75,
                mood_keywords=[mood] + list(analysis.atmospheres or [])[:3],
                reason=reason,
            )
        )

    # 1) Pure book-mood phrases first (highest priority for search order)
    for atm in (analysis.atmospheres or [])[:6]:
        add(f"{atm} piano instrumental", reason=f"atm-piano:{atm}")
        add(f"{atm} instrumental score", reason=f"atm-score:{atm}")
        add(f"{atm} ambient instrumental", reason=f"atm-amb:{atm}")

    add(f"{mood} instrumental", reason="mood")
    add(f"{mood} piano instrumental", reason="mood-piano")
    add(
        f"bittersweet {mood} instrumental".replace("bittersweet bittersweet", "bittersweet"),
        reason="mood2",
    )

    for theme in (analysis.key_themes or [])[:4]:
        t = theme.strip()
        if len(t) > 2:
            add(f"{t} instrumental piano", reason="theme")

    # 0) World-locked universe FIRST (setting/era/emotion family)
    for sq in world_locked_queries(
        analysis,
        LyricsPreference.INSTRUMENTAL_ONLY,
        max_queries=10,
    ):
        add(sq.query, reason=sq.reason or "world-lock")

    # Literary multi-dimensional cues (anti-generic differentiation)
    for tone in (analysis.dominant_tones or [])[:4]:
        add(f"{tone} instrumental", reason=f"tone:{tone}")
    if analysis.narrative_voice:
        add(f"{analysis.narrative_voice} instrumental piano", reason="voice")
    if analysis.setting_texture:
        words = " ".join(analysis.setting_texture.split()[:5])
        if len(words) > 4:
            add(f"{words} instrumental", reason="setting")
    if analysis.sensory_atmosphere:
        words = " ".join(analysis.sensory_atmosphere.split()[:5])
        if len(words) > 4:
            add(f"{words} ambient instrumental", reason="sensory")
    for style in (analysis.suitable_styles or [])[:6]:
        s = style.strip()
        if s:
            add(f"{s} instrumental" if "instrumental" not in s.lower() else s, reason="style")

    humor = getattr(analysis, "humor_level", 0.3) or 0.3
    dream = getattr(analysis, "realism_vs_dreaminess", 0.4) or 0.4
    if humor >= 0.55:
        add("playful pizzicato instrumental", reason="humor")
        add("wry light orchestral cue", reason="humor")
    if dream >= 0.6:
        add("dreamy ambient soundscape", reason="dreamy")
        add("surreal ethereal instrumental", reason="dreamy")

    realist = is_realist_literary_world(analysis)
    # 2) Band-appropriate seeds — NEVER epic/trailer banks for realist literary worlds
    if realist:
        seeds = _PASTORAL_REALIST_SEEDS + _INTIMATE_INSTRUMENTAL[:10]
        progress_label = "pastoral"
    elif band == "intimate":
        seeds = _INTIMATE_INSTRUMENTAL
        progress_label = "intimate"
    elif band == "epic" and allows_generic_cinematic_fallback(analysis):
        seeds = _EPIC_CINEMATIC + _DRAMA_CINEMATIC[:4]
        progress_label = "epic"
    else:
        seeds = _DRAMA_CINEMATIC[:4] + _INTIMATE_INSTRUMENTAL[:10]
        progress_label = "drama"

    for q in seeds:
        add(q, reason=f"band-{progress_label}")
        if len(out) >= max_queries - 2:
            break

    if analysis.era_feel:
        add(f"{analysis.era_feel} instrumental", reason="era")

    # Soft cinema only inside the book's family — never epic spectacle for realist
    if realist or band == "intimate":
        add("delicate film score piano", reason="soft-cine")
        add("nostalgic neoclassical score", reason="soft-cine")
        add("quiet documentary score", reason="soft-cine")
    elif band == "drama":
        add("emotional film score strings", reason="soft-cine")
    elif allows_generic_cinematic_fallback(analysis):
        add("epic film score instrumental", reason="soft-cine")

    return out[:max_queries]


# Backward-compatible name used by older selection code
def cinematic_instrumental_queries(
    analysis: BookVibeAnalysis,
    *,
    max_queries: int = 24,
) -> list[SearchQuerySpec]:
    return vibe_instrumental_queries(analysis, max_queries=max_queries)

_VOCAL_FRIENDLY_SEEDS = [
    "cinematic indie anthem",
    "atmospheric dream pop",
    "dark folk ballad",
    "epic rock soundtrack vibe",
    "melancholic indie folk",
    "tense electronic trip hop",
    "hopeful indie rock",
    "intimate acoustic ballad",
    "mysterious alternative rock",
    "triumphant arena rock",
    "jazzy noir lounge",
    "desert rock psychedelic",
    "ethereal art pop",
    "brooding alternative",
    "cinematic soul",
]

# Map atmosphere labels → concrete search phrases
_ATMOSPHERE_QUERIES: dict[str, list[str]] = {
    "calm": ["calm ambient instrumental", "peaceful piano atmosphere"],
    "tense": ["tense thriller score", "suspense underscore instrumental"],
    "romantic": ["romantic orchestral theme", "intimate piano romance instrumental"],
    "epic": ["epic orchestral trailer", "heroic brass film score"],
    "melancholic": ["melancholy piano instrumental", "sad cinematic strings"],
    "eerie": ["eerie ambient horror score", "dark atmospheric drone"],
    "triumphant": ["triumphant orchestral fanfare", "victory theme instrumental"],
    "intimate": ["intimate acoustic instrumental", "soft chamber strings"],
    "hopeful": ["hopeful cinematic piano", "uplifting orchestral theme"],
    "dark": ["dark ambient cinematic", "grim orchestral score"],
    "adventurous": ["adventure orchestral soundtrack", "exploration theme instrumental"],
    "nostalgic": ["nostalgic cinematic score", "wistful piano instrumental"],
    "angry": ["aggressive industrial instrumental", "intense hybrid trailer"],
    "mysterious": ["mysterious ambient score", "enigma orchestral theme"],
    "playful": ["whimsical orchestral cue", "playful pizzicato instrumental"],
    "solemn": ["solemn choral ambient", "funeral march orchestral instrumental"],
}

# Sci-fi / dense worldbuilding books benefit from these expansions
_SCIFI_EXTRA = [
    "space opera orchestral score",
    "sci-fi cinematic soundtrack",
    "desert planet ambient",
    "dune style epic score",
    "futuristic ambient soundscape",
    "sand and spice atmospheric instrumental",
    "hybrid orchestral electronic score",
]


def _inst_min(lyrics: LyricsPreference, value: float = 0.7) -> float | None:
    if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
        return value
    return None


def _flavor_query(text: str, lyrics: LyricsPreference) -> str:
    text = (text or "").strip()
    if not text:
        return "cinematic soundtrack"
    if lyrics != LyricsPreference.INSTRUMENTAL_ONLY:
        return text
    low = text.lower()
    if any(
        k in low
        for k in (
            "instrumental",
            "soundtrack",
            "score",
            "ambient",
            "orchestral",
            "piano",
            "cinematic",
            "ost",
        )
    ):
        return text
    return f"{text} instrumental"


def _spec(
    query: str,
    *,
    lyrics: LyricsPreference,
    energy: float | None = None,
    valence: float | None = None,
    reason: str = "",
    genres: list[str] | None = None,
    mood_keywords: list[str] | None = None,
    instrumentalness_min: float | None = None,
) -> SearchQuerySpec:
    return SearchQuerySpec(
        query=_flavor_query(query, lyrics),
        genres=genres or [],
        mood_keywords=mood_keywords or [],
        energy=energy,
        valence=valence,
        instrumentalness_min=instrumentalness_min
        if instrumentalness_min is not None
        else _inst_min(lyrics),
        reason=reason,
    )


_SPECTACLE_QUERY_BLOCK = (
    "harry potter",
    "hogwarts",
    "john williams",
    "two steps from hell",
    "formula 1",
    "f1 ",
    " racing",
    "sci-fi",
    "scifi",
    "space opera",
    "hybrid trailer",
    "epic battle",
    "triumphant brass",
    "war drums",
    "hans zimmer",
)


def _query_is_spectacle(text: str) -> bool:
    low = f" {text.lower()} "
    return any(k in low for k in _SPECTACLE_QUERY_BLOCK)


def expand_queries_from_analysis(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_queries: int = 24,
    cohesive_overall: bool = False,
) -> list[SearchQuerySpec]:
    """
    Build a diverse query list from LLM output + atmosphere/genre seeds.

    Genre-narrow-then-expand: world-locked setting/era/emotion queries come
    first. Spectacle / sci-fi / epic banks are skipped for realist literary
    worlds. Dedupes by normalized query text.

    When ``cohesive_overall`` is True (overall mode), skip emotional-act
    extremes far from the book's overall energy so the playlist stays one
    shuffle-friendly emotional world rather than a sequenced arc.
    """
    out: list[SearchQuerySpec] = []
    seen: set[str] = set()
    overall_e = (
        analysis.overall_energy if analysis.overall_energy is not None else 0.5
    )
    realist = is_realist_literary_world(analysis)
    allow_cinema = allows_generic_cinematic_fallback(analysis)

    def add(spec: SearchQuerySpec) -> None:
        key = " ".join(spec.query.lower().split())
        if not key or key in seen:
            return
        # Hard block spectacle language for realist / intimate literary worlds
        if (realist or not allow_cinema) and _query_is_spectacle(key):
            return
        seen.add(key)
        out.append(spec)

    # 0) World-locked universe FIRST (setting + era + emotion family)
    for sq in world_locked_queries(analysis, lyrics, max_queries=12):
        add(sq)

    # 1) LLM-provided queries (primary)
    for q in analysis.overall_search_queries:
        add(
            SearchQuerySpec(
                query=_flavor_query(q.query, lyrics),
                genres=list(q.genres or [])[:2],
                mood_keywords=list(q.mood_keywords or []),
                energy=q.energy if q.energy is not None else analysis.overall_energy,
                valence=q.valence,
                tempo_bpm=q.tempo_bpm,
                instrumentalness_min=q.instrumentalness_min
                if q.instrumentalness_min is not None
                else _inst_min(lyrics),
                acousticness=q.acousticness,
                danceability=q.danceability,
                reason=q.reason or "llm",
            )
        )

# Chapter queries flattened (useful even in overall mode as extra diversity)
    for ch in analysis.chapters[:12]:
        for q in ch.search_queries[:2]:
            add(
                SearchQuerySpec(
                    query=_flavor_query(q.query, lyrics),
                    genres=list(q.genres or [])[:1],
                    mood_keywords=list(q.mood_keywords or []),
                    energy=q.energy if q.energy is not None else ch.energy_level,
                    valence=q.valence,
                    instrumentalness_min=q.instrumentalness_min
                    if q.instrumentalness_min is not None
                    else _inst_min(lyrics),
                    reason=f"chapter:{ch.chapter_number}",
                )
            )

    # Emotional acts — only when not building a cohesive overall (shuffle) mix,
    # or when the act sits near the book's overall energy band.
    if not cohesive_overall:
        acts = list(analysis.emotional_acts or [])[:8]
    else:
        acts = [
            a
            for a in (analysis.emotional_acts or [])[:8]
            if abs((a.energy_level if a.energy_level is not None else overall_e) - overall_e)
            <= 0.22
        ][:3]
    for act in acts:
        for q in (act.search_queries or [])[:2]:
            add(
                SearchQuerySpec(
                    query=_flavor_query(q.query, lyrics),
                    genres=list(q.genres or [])[:1],
                    mood_keywords=list(q.mood_keywords or []),
                    energy=q.energy if q.energy is not None else act.energy_level,
                    valence=q.valence,
                    instrumentalness_min=q.instrumentalness_min
                    if q.instrumentalness_min is not None
                    else _inst_min(lyrics),
                    reason=f"act:{act.act_id}",
                )
            )
        if act.mood:
            add(
                _spec(
                    f"{act.mood} instrumental"
                    if lyrics.normalized().is_instrumental_only
                    else f"{act.mood} atmosphere",
                    lyrics=lyrics,
                    energy=act.energy_level,
                    reason=f"act-mood:{act.act_id}",
                    mood_keywords=[act.mood],
                )
            )

    # 2) Atmosphere-driven expansions
    atmospheres = [a.lower().strip() for a in (analysis.atmospheres or [])]
    if analysis.overall_mood:
        atmospheres.append(analysis.overall_mood.lower().strip())
    for tone in (analysis.dominant_tones or [])[:4]:
        atmospheres.append(tone.lower().strip())
    for atm in atmospheres:
        for phrase in _ATMOSPHERE_QUERIES.get(atm, []):
            add(
                _spec(
                    phrase,
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason=f"atmosphere:{atm}",
                    mood_keywords=[atm],
                )
            )
        # Fuzzy partial match on atmosphere keys
        for key, phrases in _ATMOSPHERE_QUERIES.items():
            if key in atm or atm in key:
                for phrase in phrases[:1]:
                    add(
                        _spec(
                            phrase,
                            lyrics=lyrics,
                            energy=analysis.overall_energy,
                            reason=f"atmosphere-fuzzy:{key}",
                        )
                    )

    # 3) Genre + suitable_styles seeds (styles are more specific than genres)
    style_seeds = list(analysis.suitable_styles or [])[:8] + list(
        analysis.suggested_genres or []
    )[:6]
    for g in style_seeds:
        g = g.strip()
        if not g:
            continue
        if lyrics.normalized().is_instrumental_only:
            add(
                _spec(
                    f"{g} instrumental" if "instrumental" not in g.lower() else g,
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="style-seed",
                    genres=[g],
                )
            )
            add(
                _spec(
                    f"{g} soundtrack",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="style-soundtrack",
                    genres=[g],
                )
            )
        else:
            add(
                _spec(
                    f"{g} mood music",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="style-seed",
                    genres=[g],
                )
            )

    # 4) Era / tone / voice / setting (book-specific texture)
    if analysis.era_feel:
        add(
            _spec(
                f"{analysis.era_feel} soundtrack",
                lyrics=lyrics,
                energy=analysis.overall_energy,
                reason="era-feel",
            )
        )
    if analysis.tone:
        if lyrics.normalized().is_instrumental_only:
            tone_q = (
                f"{analysis.tone} pastoral instrumental"
                if realist or not allow_cinema
                else f"{analysis.tone} cinematic instrumental"
            )
        else:
            tone_q = f"{analysis.tone} atmosphere music"
        add(
            _spec(
                tone_q,
                lyrics=lyrics,
                energy=analysis.overall_energy,
                reason="tone",
            )
        )
    if analysis.narrative_voice:
        add(
            _spec(
                f"{analysis.narrative_voice} instrumental"
                if lyrics.normalized().is_instrumental_only
                else f"{analysis.narrative_voice} indie",
                lyrics=lyrics,
                energy=analysis.overall_energy,
                reason="narrative-voice",
            )
        )
    if analysis.writing_style:
        words = " ".join(analysis.writing_style.split()[:4])
        if words:
            add(
                _spec(
                    f"{words} instrumental"
                    if lyrics.normalized().is_instrumental_only
                    else f"{words} music",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="writing-style",
                )
            )
    if analysis.setting_texture:
        words = " ".join(analysis.setting_texture.split()[:5])
        if len(words) > 4:
            add(
                _spec(
                    f"{words} instrumental"
                    if lyrics.normalized().is_instrumental_only
                    else f"{words} atmosphere",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="setting-texture",
                )
            )
    if analysis.sensory_atmosphere:
        words = " ".join(analysis.sensory_atmosphere.split()[:5])
        if len(words) > 4:
            add(
                _spec(
                    f"{words} ambient",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="sensory",
                )
            )

    # 5) Worldbuilding / genre detection from full literary blob (anti-generic:
    #    dystopian alone no longer forces dune-style desert scores;
    #    realist / historical / rural books NEVER get sci-fi or epic banks)
    blob = " ".join(analysis.vibe_keyword_pool() + [analysis.book_title or ""]).lower()
    intimacy = getattr(analysis, "intimacy_vs_epic", 0.5) or 0.5
    if not realist and allow_cinema:
        is_scifi_space = any(
            k in blob
            for k in (
                "space opera",
                "arrakis",
                "desert planet",
                "interstellar",
                "spaceship",
                "galactic",
            )
        ) or "dune" in (analysis.book_title or "").lower()
        is_scifi_broad = any(
            k in blob
            for k in ("sci-fi", "scifi", "science fiction", "cyber", "futur", "dystop")
        )
        # Epic space scores only when scale is not intimate
        if is_scifi_space or (is_scifi_broad and intimacy < 0.45):
            for phrase in _SCIFI_EXTRA:
                add(
                    _spec(
                        phrase,
                        lyrics=lyrics,
                        energy=analysis.overall_energy,
                        reason="scifi-expand",
                    )
                )
        elif is_scifi_broad and intimacy >= 0.55:
            # Intimate dystopia / literary SF → quieter, not trailer-space
            for phrase in (
                "dystopian ambient instrumental",
                "cold electronic ambient",
                "melancholic synth atmosphere",
                "surveillance tension underscore",
                "bleak piano electronic",
            ):
                add(
                    _spec(
                        phrase,
                        lyrics=lyrics,
                        energy=analysis.overall_energy,
                        reason="intimate-scifi",
                    )
                )

    # Humor / dreaminess axes
    humor = getattr(analysis, "humor_level", 0.3) or 0.3
    dream = getattr(analysis, "realism_vs_dreaminess", 0.4) or 0.4
    if humor >= 0.55:
        add(
            _spec(
                "playful whimsical instrumental"
                if lyrics.normalized().is_instrumental_only
                else "witty indie pop",
                lyrics=lyrics,
                energy=min(0.65, (analysis.overall_energy or 0.5) + 0.1),
                reason="humor-high",
            )
        )
    if dream >= 0.6:
        add(
            _spec(
                "dreamy surreal ambient"
                if lyrics.normalized().is_instrumental_only
                else "dream pop ethereal",
                lyrics=lyrics,
                energy=analysis.overall_energy,
                reason="dreamy",
            )
        )

    # 6) Energy + intimacy tier seeds (no forced epic for intimate/realist books)
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    band = _book_energy_band(analysis)
    if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental:
        if realist or band == "intimate" or energy < 0.45 or not allow_cinema:
            add(_spec("quiet ambient drone instrumental", lyrics=lyrics, energy=0.2, reason="energy-low"))
            add(_spec("intimate piano instrumental", lyrics=lyrics, energy=0.3, reason="energy-low2"))
            add(_spec("melancholic strings score", lyrics=lyrics, energy=0.35, reason="energy-low3"))
            if realist:
                add(_spec("pastoral americana instrumental", lyrics=lyrics, energy=0.35, reason="energy-pastoral"))
                add(_spec("sparse piano countryside", lyrics=lyrics, energy=0.3, reason="energy-pastoral2"))
        elif (band == "epic" or energy >= 0.72) and allow_cinema:
            add(_spec("epic battle orchestral score", lyrics=lyrics, energy=0.85, reason="energy-high"))
            add(_spec("triumphant orchestral fanfare instrumental", lyrics=lyrics, energy=0.9, reason="energy-high2"))
        else:
            add(_spec("emotional film score piano", lyrics=lyrics, energy=0.5, reason="energy-mid"))
            add(_spec("building tension hybrid score", lyrics=lyrics, energy=0.55, reason="energy-mid2"))
    else:
        if realist or band == "intimate" or energy < 0.45:
            add(_spec("quiet intimate ballad", lyrics=lyrics, energy=0.25, reason="energy-low"))
            if realist:
                add(_spec("americana folk ballad", lyrics=lyrics, energy=0.35, reason="energy-pastoral"))
        elif (band == "epic" or energy >= 0.72) and allow_cinema:
            add(_spec("high energy anthem", lyrics=lyrics, energy=0.85, reason="energy-high"))
        else:
            add(_spec("mid tempo atmospheric indie", lyrics=lyrics, energy=0.5, reason="energy-mid"))

    # Bias seed bank by band — realist stays inside pastoral family only
    if realist or not allow_cinema:
        if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental:
            ordered_seeds = list(_PASTORAL_REALIST_SEEDS) + list(_INTIMATE_INSTRUMENTAL[:8])
        else:
            ordered_seeds = [
                "melancholic indie folk",
                "americana acoustic ballad",
                "quiet pastoral songs",
                "dusty folk ballad",
            ]
    else:
        seeds = (
            _INSTRUMENTAL_SEEDS
            if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental
            else _VOCAL_FRIENDLY_SEEDS
        )
        if band == "intimate" or energy < 0.45:
            ordered_seeds = [
                s
                for s in seeds
                if not any(
                    k in s.lower()
                    for k in ("war drums", "triumphant", "epic film", "hybrid orchestral trailer")
                )
            ]
            ordered_seeds = ordered_seeds[:12] + ordered_seeds[12:]
        elif band == "epic" or energy > 0.7:
            ordered_seeds = list(reversed(seeds))
        else:
            ordered_seeds = seeds[5:] + seeds[:5]

    for phrase in ordered_seeds:
        add(_spec(phrase, lyrics=lyrics, energy=energy, reason="seed-bank"))
        if len(out) >= max_queries:
            break

    return out[:max_queries]


def expand_chapter_queries(
    chapter: ChapterVibe,
    lyrics: LyricsPreference,
    *,
    analysis: BookVibeAnalysis | None = None,
    max_queries: int = 10,
) -> list[SearchQuerySpec]:
    out: list[SearchQuerySpec] = []
    seen: set[str] = set()

    def add(spec: SearchQuerySpec) -> None:
        key = " ".join(spec.query.lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(spec)

    for q in chapter.search_queries:
        add(
            SearchQuerySpec(
                query=_flavor_query(q.query, lyrics),
                genres=list(q.genres or [])[:2],
                mood_keywords=list(q.mood_keywords or []),
                energy=q.energy if q.energy is not None else chapter.energy_level,
                valence=q.valence,
                instrumentalness_min=q.instrumentalness_min
                if q.instrumentalness_min is not None
                else _inst_min(lyrics),
                reason=q.reason or "chapter-llm",
            )
        )

    mood = chapter.mood or "cinematic"
    add(
        _spec(
            f"{mood} soundtrack instrumental"
            if lyrics == LyricsPreference.INSTRUMENTAL_ONLY
            else f"{mood} atmosphere",
            lyrics=lyrics,
            energy=chapter.energy_level,
            reason="chapter-mood",
        )
    )
    for atm in (chapter.atmospheres or [])[:3]:
        for phrase in _ATMOSPHERE_QUERIES.get(atm.lower(), [f"{atm} instrumental"])[:2]:
            add(
                _spec(
                    phrase,
                    lyrics=lyrics,
                    energy=chapter.energy_level,
                    reason=f"chapter-atm:{atm}",
                )
            )

    if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
        # Never inject generic cinema for realist / intimate literary worlds
        if analysis is not None and (
            is_realist_literary_world(analysis)
            or not allows_generic_cinematic_fallback(analysis)
        ):
            add(
                _spec(
                    "pastoral chamber instrumental",
                    lyrics=lyrics,
                    energy=chapter.energy_level,
                    reason="chapter-pastoral",
                )
            )
        else:
            add(
                _spec(
                    "cinematic orchestral score",
                    lyrics=lyrics,
                    energy=chapter.energy_level,
                    reason="chapter-cinematic",
                )
            )

    return out[:max_queries]


def cinematic_fallback_queries(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_queries: int = 16,
) -> list[SearchQuerySpec]:
    """
    Last-resort queries — world-gated.

    Realist / historical / rural / intimate literary books get pastoral
    expansion only (never Williams / Potter / Zimmer / racing / sci-fi banks).
    Returns [] when even that would fight the book; patience > fill.
    """
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    mood = (analysis.overall_mood or "reflective").lower()
    title = (analysis.book_title or "").lower()

    # Hard block generic spectacle cinema for the wrong world
    if not allows_generic_cinematic_fallback(analysis) or is_realist_literary_world(analysis):
        # Stay inside pastoral / quiet documentary family only
        if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental:
            base = list(_PASTORAL_REALIST_SEEDS)
            if energy < 0.45:
                base = ["nils frahm", "max richter", "peaceful piano soundtrack"] + base
            # Book-specific gentle hooks (never spectacle)
            if "eden" in title or "steinbeck" in " ".join(analysis.authors or []).lower():
                base = [
                    "pastoral california instrumental",
                    "salinas valley score",
                    "dust bowl acoustic instrumental",
                ] + base
            if "gatsby" in title:
                base = ["jazz age instrumental", "1920s jazz instrumental"] + base
        else:
            base = [
                "melancholic indie folk",
                "americana acoustic ballad",
                "quiet pastoral songs",
                f"{mood} folk",
            ]
        return [
            _spec(q, lyrics=lyrics, energy=min(energy, 0.5), reason="pastoral-fallback")
            for q in base[:max_queries]
        ]

    # Spectacle-allowed worlds only (mythic / high-adventure / some SF)
    if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
        base = [
            "ludovico einaudi",
            "max richter",
            "thomas newman",
            "ambient cinematic",
            "emotional orchestral",
            "post rock instrumental",
            f"{mood} film score",
            f"{mood} orchestral instrumental",
        ]
        # Zimmer / Williams / trailer only when world truly warrants epic scale
        intimacy = analysis.intimacy_vs_epic if analysis.intimacy_vs_epic is not None else 0.5
        if intimacy <= 0.35 and energy >= 0.55:
            base.extend(
                [
                    "hans zimmer",
                    "howard shore",
                    "adventure film score",
                ]
            )
        if energy > 0.72 and intimacy <= 0.3:
            base.extend(["two steps from hell", "epic orchestral soundtrack"])
        elif energy < 0.4:
            base.extend(["nils frahm", "peaceful piano soundtrack"])
        if "dune" in title:
            base = ["hans zimmer dune", "dune soundtrack", "dune part two score"] + base
        # Never auto-inject Harry Potter / racing / generic Williams magic
    else:
        base = [
            "atmospheric alternative",
            "indie folk atmospheric",
            f"{mood} songs",
            "emotional film songs",
            "dreamy alternative rock",
        ]

    return [
        _spec(q, lyrics=lyrics, energy=energy, reason="cinematic-fallback")
        for q in base[:max_queries]
    ]


def broaden_specs(specs: list[SearchQuerySpec], lyrics: LyricsPreference) -> list[SearchQuerySpec]:
    """
    Produce simpler, higher-recall variants of existing queries
    (drop multi-clause phrases, keep 2–4 head words + instrumental cue).
    """
    out: list[SearchQuerySpec] = []
    seen: set[str] = set()
    for s in specs:
        words = [w for w in s.query.split() if w.lower() not in {"the", "a", "an", "of", "and"}]
        short = " ".join(words[:3])
        if lyrics == LyricsPreference.INSTRUMENTAL_ONLY and "instrumental" not in short.lower():
            # Pair with high-recall suffixes
            variants = [
                f"{short} instrumental",
                f"{short} soundtrack",
                f"{words[0]} orchestral" if words else "orchestral score",
            ]
        else:
            variants = [short, f"{short} music"]

        for v in variants:
            key = v.lower().strip()
            if key in seen or len(key) < 4:
                continue
            seen.add(key)
            out.append(
                SearchQuerySpec(
                    query=v,
                    energy=s.energy,
                    valence=s.valence,
                    instrumentalness_min=_inst_min(lyrics, 0.5),
                    mood_keywords=list(s.mood_keywords or [])[:3],
                    reason=f"broaden:{s.reason or 'query'}",
                )
            )
    return out
