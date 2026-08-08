"""Expand vibe analysis into a rich, diverse set of Spotify search queries."""

from __future__ import annotations

from chapterscore.models import (
    BookVibeAnalysis,
    ChapterVibe,
    LyricsPreference,
    SearchQuerySpec,
)

# Seed banks used when LLM queries are sparse or need broadening.
_INSTRUMENTAL_SEEDS = [
    "cinematic orchestral soundtrack",
    "epic film score instrumental",
    "dark ambient atmosphere",
    "tense thriller underscore",
    "melancholic piano instrumental",
    "post rock instrumental build",
    "neoclassical piano strings",
    "desert ambient soundscape",
    "adventure orchestral theme",
    "mysterious ambient drone",
    "triumphant brass fanfare instrumental",
    "intimate acoustic instrumental",
    "hybrid orchestral trailer music",
    "space ambient atmospheric",
    "ethnic world fusion instrumental",
    "choir ethereal ambient no vocals",
    "war drums epic percussion instrumental",
    "hopeful cinematic piano",
    "ominous low brass score",
    "quiet reflective guitar instrumental",
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
    e = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    atms = {a.lower() for a in (analysis.atmospheres or [])}
    intimate_keys = {
        "intimate",
        "melancholic",
        "nostalgic",
        "hopeful",
        "playful",
        "romantic",
        "calm",
        "solemn",
    }
    epic_keys = {"epic", "triumphant", "adventurous", "angry", "tense"}
    if e <= 0.45 or (atms & intimate_keys and e < 0.65):
        if atms & epic_keys and e >= 0.55:
            return "drama"
        return "intimate"
    if e >= 0.72 or (atms & epic_keys and e >= 0.6):
        return "epic"
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
    add(f"bittersweet {mood} instrumental".replace("bittersweet bittersweet", "bittersweet"), reason="mood2")

    for theme in (analysis.key_themes or [])[:4]:
        t = theme.strip()
        if len(t) > 2:
            add(f"{t} instrumental piano", reason="theme")

    # 2) Band-appropriate artist/style seeds (not always epic)
    if band == "intimate":
        seeds = _INTIMATE_INSTRUMENTAL
        progress_label = "intimate"
    elif band == "epic":
        seeds = _EPIC_CINEMATIC + _DRAMA_CINEMATIC[:4]
        progress_label = "epic"
    else:
        seeds = _DRAMA_CINEMATIC + _INTIMATE_INSTRUMENTAL[:8]
        progress_label = "drama"

    for q in seeds:
        add(q, reason=f"band-{progress_label}")
        if len(out) >= max_queries - 2:
            break

    if analysis.era_feel:
        add(f"{analysis.era_feel} instrumental", reason="era")

    # Light cinematic only when it fits the band (not forced for intimate books)
    if band == "intimate":
        add("delicate film score piano", reason="soft-cine")
        add("nostalgic neoclassical score", reason="soft-cine")
    elif band == "drama":
        add("emotional film score strings", reason="soft-cine")
    else:
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


def expand_queries_from_analysis(
    analysis: BookVibeAnalysis,
    lyrics: LyricsPreference,
    *,
    max_queries: int = 24,
) -> list[SearchQuerySpec]:
    """
    Build a diverse query list from LLM output + atmosphere/genre seeds.

    Dedupes by normalized query text while preserving order (LLM first).
    """
    out: list[SearchQuerySpec] = []
    seen: set[str] = set()

    def add(spec: SearchQuerySpec) -> None:
        key = " ".join(spec.query.lower().split())
        if not key or key in seen:
            return
        seen.add(key)
        out.append(spec)

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

    # 2) Atmosphere-driven expansions
    atmospheres = [a.lower().strip() for a in (analysis.atmospheres or [])]
    if analysis.overall_mood:
        atmospheres.append(analysis.overall_mood.lower().strip())
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

    # 3) Genre seeds from analysis
    for g in (analysis.suggested_genres or [])[:8]:
        g = g.strip()
        if not g:
            continue
        if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
            add(
                _spec(
                    f"{g} instrumental",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="genre-seed",
                    genres=[g],
                )
            )
            add(
                _spec(
                    f"{g} soundtrack",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="genre-soundtrack",
                    genres=[g],
                )
            )
        else:
            add(
                _spec(
                    f"{g} mood music",
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="genre-seed",
                    genres=[g],
                )
            )

    # 4) Era / tone
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
        add(
            _spec(
                f"{analysis.tone} cinematic instrumental"
                if lyrics == LyricsPreference.INSTRUMENTAL_ONLY
                else f"{analysis.tone} atmosphere music",
                lyrics=lyrics,
                energy=analysis.overall_energy,
                reason="tone",
            )
        )

    # 5) Sci-fi / dense worldbuilding detection
    blob = " ".join(
        [
            analysis.overall_mood or "",
            analysis.era_feel or "",
            analysis.tone or "",
            " ".join(analysis.key_themes or []),
            " ".join(analysis.atmospheres or []),
            analysis.book_title or "",
        ]
    ).lower()
    if any(
        k in blob
        for k in (
            "sci-fi",
            "scifi",
            "science fiction",
            "space",
            "desert",
            "dystop",
            "empire",
            "planet",
            "arrakis",
            "cyber",
            "futur",
        )
    ) or "dune" in (analysis.book_title or "").lower():
        for phrase in _SCIFI_EXTRA:
            add(
                _spec(
                    phrase,
                    lyrics=lyrics,
                    energy=analysis.overall_energy,
                    reason="scifi-expand",
                )
            )

    # 6) Energy-tier seeds — only add tiers near the book's energy (no forced epic)
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental:
        if energy < 0.45:
            add(_spec("quiet ambient drone instrumental", lyrics=lyrics, energy=0.2, reason="energy-low"))
            add(_spec("intimate piano instrumental", lyrics=lyrics, energy=0.3, reason="energy-low2"))
            add(_spec("melancholic strings score", lyrics=lyrics, energy=0.35, reason="energy-low3"))
        elif energy < 0.7:
            add(_spec("emotional film score piano", lyrics=lyrics, energy=0.5, reason="energy-mid"))
            add(_spec("building tension hybrid score", lyrics=lyrics, energy=0.55, reason="energy-mid2"))
        else:
            add(_spec("epic battle orchestral score", lyrics=lyrics, energy=0.85, reason="energy-high"))
            add(_spec("triumphant orchestral fanfare instrumental", lyrics=lyrics, energy=0.9, reason="energy-high2"))
    else:
        if energy < 0.45:
            add(_spec("quiet intimate ballad", lyrics=lyrics, energy=0.25, reason="energy-low"))
        elif energy < 0.7:
            add(_spec("mid tempo atmospheric indie", lyrics=lyrics, energy=0.5, reason="energy-mid"))
        else:
            add(_spec("high energy anthem", lyrics=lyrics, energy=0.85, reason="energy-high"))

    # Bias seed bank by overall energy (instrumental seeds: front = calmer)
    seeds = (
        _INSTRUMENTAL_SEEDS
        if lyrics.normalized().is_instrumental_only or lyrics.prefers_instrumental
        else _VOCAL_FRIENDLY_SEEDS
    )
    if energy < 0.45:
        ordered_seeds = seeds[:10] + seeds[10:]
    elif energy > 0.7:
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
    Last-resort high-recall queries: popular film/game score language that
    still tilts toward the book's mood/energy.
    """
    energy = analysis.overall_energy if analysis.overall_energy is not None else 0.5
    mood = (analysis.overall_mood or "cinematic").lower()
    base: list[str]
    if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
        base = [
            "hans zimmer",
            "howard shore",
            "john williams",
            "ludovico einaudi",
            "max richter",
            "ramin djawadi",
            "cinematic orchestra",
            "movie soundtrack instrumental",
            "ambient cinematic",
            "dark cinematic score",
            "adventure film score",
            "emotional orchestral",
            "game soundtrack orchestral",
            "post rock instrumental",
            f"{mood} film score",
            f"{mood} orchestral instrumental",
        ]
        if energy > 0.65:
            base.extend(["two steps from hell", "epic orchestral soundtrack"])
        elif energy < 0.4:
            base.extend(["nils frahm", "peaceful piano soundtrack"])
        title = (analysis.book_title or "").lower()
        if "dune" in title:
            base = ["hans zimmer dune", "dune soundtrack", "dune part two score"] + base
        if "gatsby" in title:
            base = ["jazz age instrumental", "1920s jazz instrumental", "art deco jazz"] + base
    else:
        base = [
            "cinematic indie soundtrack",
            f"{mood} songs",
            "atmospheric alternative",
            "epic soundtrack songs",
            "emotional film songs",
            "indie folk atmospheric",
            "dark pop cinematic",
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
