"""Intelligent track ranking: relevance + popularity + diversity + lyrics fit.

Instrumental filtering uses progressive strictness levels so discovery can
relax automatically when the candidate pool is too small.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from enum import IntEnum

from chapterscore.models import LyricsPreference, RankedTrack, SearchQuerySpec


class InstrumentalStrictness(IntEnum):
    """How aggressively to filter for instrumental / non-vocal tracks."""

    STRICT = 0  # high instrumentalness or strong title cues
    MODERATE = 1  # mid instrumentalness OR soundtrack-ish provenance
    RELAXED = 2  # only reject clear vocals / speech
    PERMISSIVE = 3  # only reject karaoke/junk; score prefers instrumental


# Heuristic title/artist cues
_INSTRUMENTAL_CUES = re.compile(
    r"\b("
    r"instrumental|orchestral|orchestra|soundtrack|ost|score|theme|"
    r"suite|overture|prelude|interlude|nocturne|sonata|concerto|"
    r"piano\s*(version|solo|theme)?|ambient|cinematic|film\s*score|"
    r"no\s*vocals?|without\s*vocals?|underscore|end\s*credits|"
    r"main\s*title|opening\s*titles|closing\s*titles"
    r")\b",
    re.IGNORECASE,
)
_VOCAL_HARD = re.compile(
    r"\b("
    r"lyrics|with\s+vocals?|a\s*cappella|acapella|sing[- ]?along|"
    r"karaoke|radio\s*edit|official\s*video|music\s*video|"
    r"vocal\s*version|sung\s*version| sing\b|sings\b|singer\b|"
    r"rap\b|rapping|hip[\s-]?hop|r&b|rnb\b|pop\s*hit|"
    r"feat\.|ft\.|featuring"
    r")\b",
    re.IGNORECASE,
)
_VOCAL_SOFT = re.compile(
    r"\b(remix|cover|radio|live\s*session|unplugged)\b",
    re.IGNORECASE,
)
# Genres/styles that almost always imply sung vocals — hard reject in instrumental-only
_VOCAL_GENRE_BLOCK = re.compile(
    r"\b("
    r"country|bluegrass|honky[\s-]?tonk|nashville|"
    r"hip[\s-]?hop|trap\b|drill\b|grime\b|"
    r"k-?pop|j-?pop|boy\s*band|girl\s*group|"
    r"reggae|dancehall|soca|"
    r"death\s*metal|black\s*metal|screamo|hardcore\s*punk|"
    r"gospel|worship\s*song|christian\s*rock|"
    r"opera\s*aria"  # sung opera — keep "orchestral" separate
    r")\b",
    re.IGNORECASE,
)
_UNDESIRABLE = re.compile(
    r"\b("
    r"karaoke|tribute\s*band|midi|ringtone|8-?bit|chipmunk|"
    r"slowed\s*\+?\s*reverb|nightcore|screwed|white\s*noise|"
    r"sleep\s*sounds?|rain\s*sounds?|fan\s*noise|"
    r"royalty\s*free|copyright\s*free|no\s*copyright|ncs\b|"
    r"stock\s*music|background\s*music|youtube\s*audio|free\s*music|"
    r"energy\s*sound|ashamaluev|soundstripe"
    r")\b",
    re.IGNORECASE,
)
_LOW_QUALITY = re.compile(
    r"\b("
    r"epic\s+version|epic\s+cover|piano\s+cover|violin\s+cover|"
    r"music\s+for\s+videos?|vlog\s+music|meditation\s+music\s+for|"
    r"1\s*hour|10\s*hours|white\s*noise"
    r")\b",
    re.IGNORECASE,
)
_SOUNDTRACK_QUERY = re.compile(
    r"\b(instrumental|soundtrack|score|ost|ambient|orchestral|cinematic|"
    r"neoclassical|post-?rock|piano|film)\b",
    re.IGNORECASE,
)

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "feat",
        "ft",
        "by",
        "from",
        "version",
        "remix",
        "edit",
        "original",
        "motion",
        "picture",
    }
)


# Artists strongly associated with film/game scores & instrumental work
_SCORE_ARTISTS = re.compile(
    r"("
    r"hans zimmer|john williams|howard shore|james newton howard|thomas newman|"
    r"ramin djawadi|ludwig g[öo]ransson|junkie xl|two steps from hell|"
    r"audiomachine|really slow motion|immediate music|position music|"
    r"vangelis|yiruma|ludovico einaudi|max richter|nils frahm|"
    r"explosions in the sky|godspeed you|mogwai|this will destroy you|"
    r"brian eno|stars of the lid|tim hecker|william basinski|"
    r"secret garden|the piano guys|city of prague|london symphony|"
    r"bear mccreary|alan silvestri|danny elfman|james horner|"
    r"joe hisaishi|ryuichi sakamoto|cliff martinez|"
    r"j[óo]hann j[óo]hannsson|hildur gu[ðd]nad[óo]ttir|"
    r"carter burwell|michael giacchino|alexandre desplat|john powell|"
    r"steve jablonsky|harry gregson[- ]williams|lisa gerrard|"
    r"dead can dance|carbon based lifeforms|solar fields|aes dana|"
    r"philip glass|arvo p[äa]rt|eric whitacre|ólafur arnalds|ólöf arnalds|"
    r"kiasmos|a winged victory for the sullen|"
    # Extra cinematic / trailer universe
    r"hans zimmer|klaus badelt|heitor pereira|lorne balfe|rupert gregson[- ]williams|"
    r"brian tyler|marco beltrami|tyler bates|jed kurzel|johann johannsson|"
    r"dario marianelli|patrick doyle|nicholas hooper|alexandre desplat|"
    r"john debney|jerry goldsmith|ennio morricone|basil poledouris|"
    r"trevor morris|brian eno|raphael beau|austin wintory|gareth coker|"
    r"inon zur|jeremy soule|yasunori mitsuda|nobuo uematsu|"
    r"thomas bergersen|nick phoenix|globus|really slow motion|"
    r"brand x music|elephant music|corner stone cinematic"
    r")",
    re.IGNORECASE,
)

_CINEMATIC_ALBUM = re.compile(
    r"("
    r"soundtrack|motion\s*picture|original\s*score|ost\b|film\s*score|"
    r"television\s*series|video\s*game|game\s*soundtrack|"
    r"interstellar|inception|dune|avatar|gladiator|pirates\s*of\s*the\s*caribbean|"
    r"harry\s*potter|lord\s*of\s*the\s*rings|the\s*hobbit|star\s*wars|"
    r"dark\s*knight|batman|man\s*of\s*steel|blade\s*runner|"
    r"last\s*of\s*us|god\s*of\s*war|skyrim|zelda|final\s*fantasy"
    r")",
    re.IGNORECASE,
)


def _artist_blob(track: RankedTrack) -> str:
    return " ".join(track.artists or [])


def is_likely_instrumental(track: RankedTrack) -> bool | None:
    """
    Ternary detector: True / False / None (unknown).

    When audio features are unavailable (common 403), rely on title, album,
    and known score artists — **never** on the search query string alone.
    """
    inst = track.features.get("instrumentalness")
    speech = track.features.get("speechiness")
    name = track.name or ""
    album = track.album or ""
    blob = f"{name} {album}"
    artists = _artist_blob(track)

    if _VOCAL_HARD.search(blob) or _VOCAL_GENRE_BLOCK.search(blob):
        return False

    if inst is not None:
        if inst >= 0.75:
            return True
        if inst >= 0.55 and (speech is None or speech < 0.08):
            return True
        if inst <= 0.35:
            return False
        if inst <= 0.50 and speech is not None and speech > 0.12:
            return False

    if speech is not None and speech > 0.15:
        return False

    if _INSTRUMENTAL_CUES.search(blob) or _CINEMATIC_ALBUM.search(blob):
        if inst is not None and inst < 0.25:
            return False
        return True

    if _SCORE_ARTISTS.search(artists) or _SCORE_ARTISTS.search(album):
        if _VOCAL_HARD.search(name):
            return False
        return True

    return None


def has_track_level_instrumental_signal(track: RankedTrack) -> bool:
    """
    Positive evidence ON THE TRACK (not the search query) that it is instrumental/cinematic.

    Critical: matched_query must never be used as a free pass — that caused vocal
    tracks from soundtrack-flavored searches to leak through.
    """
    name = track.name or ""
    album = track.album or ""
    artists = _artist_blob(track)
    blob = f"{name} {album}"
    inst = track.features.get("instrumentalness")
    speech = track.features.get("speechiness")

    if inst is not None and inst >= 0.70 and (speech is None or speech < 0.12):
        return True
    if _INSTRUMENTAL_CUES.search(blob):
        return True
    if _CINEMATIC_ALBUM.search(album) or _CINEMATIC_ALBUM.search(name):
        return True
    if _SCORE_ARTISTS.search(artists):
        return True
    return False


def cinematic_fit(track: RankedTrack) -> float:
    """0–1 how strongly this track sits in the film-score / cinematic universe."""
    score = 0.0
    artists = _artist_blob(track)
    blob = f"{track.name or ''} {track.album or ''}"
    if _SCORE_ARTISTS.search(artists):
        score += 0.55
    if _CINEMATIC_ALBUM.search(blob):
        score += 0.35
    if _INSTRUMENTAL_CUES.search(blob):
        score += 0.15
    inst = track.features.get("instrumentalness")
    if inst is not None:
        score += 0.25 * min(1.0, inst)
    return min(1.0, score)


def _query_is_instrumental_flavored(query: str) -> bool:
    return bool(_SOUNDTRACK_QUERY.search(query or ""))


def is_undesirable(track: RankedTrack) -> bool:
    blob = f"{track.name or ''} {track.album or ''} {' '.join(track.artists or [])}"
    if _UNDESIRABLE.search(blob):
        return True
    return False


def quality_penalty(track: RankedTrack, *, popularity_known: bool = True) -> float:
    """Return a multiplicative score factor for catalogue quality signals."""
    factor = 1.0
    blob = f"{track.name or ''} {' '.join(track.artists or [])}"
    if _LOW_QUALITY.search(blob):
        factor *= 0.45
    if popularity_known:
        if track.popularity <= 0:
            factor *= 0.55
        elif track.popularity < 15:
            factor *= 0.75
        elif track.popularity < 30:
            factor *= 0.9
    # Mild quality cues only — book vibe multiplier decides epic vs intimate fit
    if _SCORE_ARTISTS.search(" ".join(track.artists or [])):
        factor *= 1.12
    album = track.album or ""
    if _CINEMATIC_ALBUM.search(album):
        factor *= 1.1
    elif any(k in album.lower() for k in ("soundtrack", "motion picture", "score", "ost")):
        factor *= 1.08
    return min(1.6, factor)


def passes_lyrics_filter(
    track: RankedTrack,
    lyrics: LyricsPreference,
    *,
    strictness: InstrumentalStrictness = InstrumentalStrictness.STRICT,
) -> bool:
    """
    Priority-1 HARD filter for vocal policy.

    INSTRUMENTAL_ONLY is intentionally harsh: prefer empty pool over vocals.
    Track-level evidence is required — search-query wording is never enough.
    """
    if is_undesirable(track):
        return False

    mode = lyrics.normalized()
    name = track.name or ""
    album = track.album or ""
    artists = _artist_blob(track)
    blob = f"{name} {album} {artists}"
    inst = track.features.get("instrumentalness")
    speech = track.features.get("speechiness")
    likely = is_likely_instrumental(track)

    if mode is LyricsPreference.ALLOW_LYRICS:
        return True

    if mode is LyricsPreference.PREFER_INSTRUMENTAL:
        if "karaoke" in name.lower() or _UNDESIRABLE.search(blob):
            return False
        return True

    # ── INSTRUMENTAL_ONLY (very strict hard filter) ───────────────────────
    # Hard negatives — never admit these at any strictness level
    if _VOCAL_HARD.search(blob) or _VOCAL_GENRE_BLOCK.search(blob):
        return False
    if speech is not None and speech > 0.18:
        return False
    if likely is False:
        return False
    if inst is not None and inst < 0.50:
        return False
    if _VOCAL_SOFT.search(name) and not has_track_level_instrumental_signal(track):
        return False

    # Thresholds by progressive strictness (only relax uncertainty, not vocals)
    if strictness == InstrumentalStrictness.STRICT:
        if speech is not None and speech > 0.10:
            return False
        if inst is not None:
            return inst >= 0.75 and (speech is None or speech < 0.10)
        # No features: require strong track-level cinematic/instrumental evidence
        return has_track_level_instrumental_signal(track) and likely is not False

    if strictness == InstrumentalStrictness.MODERATE:
        if speech is not None and speech > 0.14:
            return False
        if inst is not None:
            return inst >= 0.62
        return has_track_level_instrumental_signal(track)

    if strictness == InstrumentalStrictness.RELAXED:
        if speech is not None and speech > 0.16:
            return False
        if inst is not None and inst < 0.55:
            return False
        # Still need positive track-level signal
        return has_track_level_instrumental_signal(track) or (
            likely is True and _SCORE_ARTISTS.search(artists)
        )

    # PERMISSIVE last resort — still quality-first, still no clear vocals
    if inst is not None and inst < 0.50:
        return False
    if likely is False:
        return False
    return has_track_level_instrumental_signal(track) or likely is True


# Genre / style clash tokens (normalized lowercase substrings)
_STYLE_ALIASES: dict[str, tuple[str, ...]] = {
    "country": ("country", "nashville", "honky", "bluegrass", "americana vocal"),
    "bubblegum": ("bubblegum", "teen pop", "boy band"),
    "reggae": ("reggae", "dancehall", "dub vocal"),
    "edm": ("edm", "big room", "festival drop"),
    "metal": ("death metal", "black metal", "screamo"),
    "rap": ("hip hop", "hip-hop", "rap ", "trap ", "drill "),
    "comedy": ("comedy", "parody", "novelty"),
    "children": ("kids ", "children", "nursery"),
    "gospel": ("gospel choir vocal", "worship vocal"),
}


def style_clash_score(
    track: RankedTrack,
    *,
    suitable: list[str] | None = None,
    avoid: list[str] | None = None,
) -> float:
    """
    Return a multiplier in ~[0.15, 1.25] for book-style fit.

    Priority 2 after lyrics: heavily penalize avoid_styles, boost suitable.
    """
    blob = f"{track.name} {track.album} {' '.join(track.artists)} {track.matched_query}".lower()
    mult = 1.0

    for style in avoid or []:
        s = style.lower().strip()
        if not s:
            continue
        aliases = _STYLE_ALIASES.get(s, ())
        tokens = (s,) + aliases
        if any(tok in blob for tok in tokens if len(tok) >= 3):
            mult *= 0.2  # hard stylistic clash
            break

    hits = 0
    for style in suitable or []:
        s = style.lower().strip()
        if not s:
            continue
        if s in blob or any(w in blob for w in s.split() if len(w) > 3):
            hits += 1
    if hits:
        mult *= min(1.25, 1.0 + 0.08 * hits)

    # Soft boost for score artists only when suitable styles invite them
    suitable_l = " ".join(suitable or []).lower()
    if any(
        k in suitable_l
        for k in (
            "orchestral",
            "soundtrack",
            "ambient",
            "cinematic",
            "score",
            "neoclassical",
            "piano",
        )
    ):
        if _SCORE_ARTISTS.search(" ".join(track.artists or "")):
            mult *= 1.08

    return max(0.15, min(1.35, mult))


_EPIC_TRAILER_MARKERS = re.compile(
    r"("
    r"two steps from hell|thomas bergersen|audiomachine|immediate music|"
    r"pirates of the caribbean|lord of the rings|gladiator|dark knight|"
    r"hunger games|man of steel|transformers|avengers|battle|war drums|"
    r"trailer music|epic orchestral|hybrid trailer|brass fanfare|"
    r"klaus badelt|steve jablonsky"
    r")",
    re.IGNORECASE,
)

_INTIMATE_MARKERS = re.compile(
    r"("
    r"max richter|nils frahm|olafur arnalds|ólafur arnalds|ludovico einaudi|yann tiersen|"
    r"yiruma|library tapes|dustin o.?halloran|winged victory|"
    r"piano|nocturne|lullaby|intimate|delicate|quiet|soft|"
    r"chamber|neoclassical|bittersweet|nostalg"
    r")",
    re.IGNORECASE,
)


_DREAMY_MARKERS = re.compile(
    r"("
    r"dream|ethereal|surreal|ambient|hazy|shoegaze|reverb|"
    r"brian eno|stars of the lid|tim hecker|floating"
    r")",
    re.IGNORECASE,
)
_PLAYFUL_MARKERS = re.compile(
    r"("
    r"playful|whimsical|pizzicato|quirky|lighthearted|comic|"
    r"jaunty|bouncy|wry"
    r")",
    re.IGNORECASE,
)


def book_vibe_multiplier(
    track: RankedTrack,
    *,
    book_energy: float | None = None,
    atmospheres: list[str] | None = None,
    overall_mood: str | None = None,
    key_themes: list[str] | None = None,
    intimacy_vs_epic: float | None = None,
    narrative_voice: str | None = None,
    distinctive_signature: str | None = None,
    setting_texture: str | None = None,
    dominant_tones: list[str] | None = None,
    humor_level: float | None = None,
    realism_vs_dreaminess: float | None = None,
    anti_generic_notes: list[str] | None = None,
    vibe_keywords: list[str] | None = None,
) -> float:
    """
    Multiplier ~[0.18, 1.45] for how well a track fits the book's emotional world.

    Uses multi-dimensional literary signals (intimacy scale, voice, humor,
    dreaminess) so same-genre books don't collapse to identical rankings.
    """
    energy = 0.5 if book_energy is None else float(book_energy)
    intimacy = 0.5 if intimacy_vs_epic is None else float(intimacy_vs_epic)
    humor = 0.3 if humor_level is None else float(humor_level)
    dream = 0.4 if realism_vs_dreaminess is None else float(realism_vs_dreaminess)
    atms = {a.lower() for a in (atmospheres or [])}
    tones = {t.lower() for t in (dominant_tones or [])}
    mood = (overall_mood or "").lower()
    themes = " ".join(key_themes or []).lower()
    voice = (narrative_voice or "").lower()
    signature = (distinctive_signature or "").lower()
    setting = (setting_texture or "").lower()
    anti = " ".join(anti_generic_notes or []).lower()
    blob = f"{track.name} {track.album} {' '.join(track.artists)} {track.matched_query}".lower()

    mult = 1.0
    intimate_book = (
        intimacy >= 0.6
        or energy <= 0.45
        or bool(
            atms
            & {
                "intimate",
                "melancholic",
                "nostalgic",
                "hopeful",
                "playful",
                "romantic",
                "calm",
                "bittersweet",
            }
        )
        or any(k in voice for k in ("intimate", "confessional", "wry", "earnest"))
    )
    epic_book = (
        intimacy <= 0.35
        or energy >= 0.72
        or bool(atms & {"epic", "triumphant", "adventurous", "angry"})
    ) and intimacy < 0.55
    blocks_epic = any(
        k in anti for k in ("not epic", "no epic", "not trailer", "no trailer", "not battle")
    )
    if blocks_epic:
        intimate_book = True
        epic_book = False

    # Token overlap from rich literary pool
    vibe_tokens: set[str] = set()
    for source in (
        list(atms),
        list(tones),
        [mood, themes, voice, signature, setting],
        vibe_keywords or [],
    ):
        if isinstance(source, list):
            for item in source:
                vibe_tokens.update(w for w in re.findall(r"[a-z]+", str(item).lower()) if len(w) > 3)
        else:
            vibe_tokens.update(w for w in re.findall(r"[a-z]+", str(source).lower()) if len(w) > 3)
    vibe_tokens -= {
        "music",
        "book",
        "story",
        "novel",
        "life",
        "time",
        "world",
        "that",
        "this",
        "with",
        "from",
        "than",
        "into",
        "about",
        "other",
        "genre",
        "typical",
        "rather",
    }
    hits = sum(1 for t in vibe_tokens if t in blob)
    if hits:
        mult *= min(1.4, 1.0 + 0.06 * hits)

    t_energy = track.features.get("energy")
    if t_energy is not None:
        gap = abs(t_energy - energy)
        if gap > 0.35:
            mult *= max(0.35, 1.0 - 1.1 * (gap - 0.35))
        elif gap < 0.15:
            mult *= 1.08

    if intimate_book and not epic_book:
        if _EPIC_TRAILER_MARKERS.search(blob):
            mult *= 0.18 if intimacy >= 0.7 else 0.22
        if re.search(
            r"hans zimmer|two steps|john williams|howard shore",
            blob,
            re.I,
        ) and not _INTIMATE_MARKERS.search(blob):
            if re.search(
                r"battle|pirates|gladiator|dark knight|inception\s*main|\btime\b",
                blob,
                re.I,
            ):
                mult *= 0.32
            elif energy < 0.5 or intimacy >= 0.6:
                mult *= 0.5
        if _INTIMATE_MARKERS.search(blob):
            mult *= 1.25 if intimacy >= 0.65 else 1.2

    if epic_book and _EPIC_TRAILER_MARKERS.search(blob):
        mult *= 1.15
    if epic_book and _INTIMATE_MARKERS.search(blob) and energy > 0.75:
        mult *= 0.85

    # Humor / irony → reward playful cues, penalize solemn epic when comedy-forward
    if humor >= 0.55:
        if _PLAYFUL_MARKERS.search(blob):
            mult *= 1.18
        if _EPIC_TRAILER_MARKERS.search(blob) and intimacy > 0.4:
            mult *= 0.55

    # Dreamy / surreal books → ambient/ethereal fit
    if dream >= 0.6:
        if _DREAMY_MARKERS.search(blob):
            mult *= 1.15
        if _EPIC_TRAILER_MARKERS.search(blob) and intimacy > 0.45:
            mult *= 0.7

    return max(0.18, min(1.45, mult))


def _feature_distance(actual: float | None, target: float | None, weight: float = 1.0) -> float:
    if actual is None or target is None:
        return 0.55  # slightly optimistic neutral — don't punish missing features
    return max(0.0, 1.0 - abs(actual - target)) * weight


def _keyword_overlap(spec: SearchQuerySpec, track: RankedTrack) -> float:
    """
    Soft keyword overlap. Genre/mood words rarely appear in track titles,
    so we floor the score and weight mood keywords lightly.
    """
    q_raw = f"{spec.query} {' '.join(spec.mood_keywords)} {' '.join(spec.genres)}"
    q_tokens = set(re.findall(r"[a-z0-9]+", q_raw.lower())) - _STOP
    t_tokens = set(
        re.findall(r"[a-z0-9]+", f"{track.name} {' '.join(track.artists)} {track.album}".lower())
    ) - _STOP

    if not q_tokens:
        return 0.5

    # Prefer meaningful overlaps; don't require most query tokens
    hits = len(q_tokens & t_tokens)
    raw = hits / max(3, min(len(q_tokens), 6))  # normalize vs ~3–6 expected tokens
    # Floor at 0.35 so low lexical overlap doesn't kill good feature matches
    return min(1.0, 0.35 + 0.65 * min(1.0, raw))


def score_track(
    track: RankedTrack,
    spec: SearchQuerySpec,
    lyrics: LyricsPreference,
    *,
    artist_counts: Counter[str] | None = None,
    seen_ids: set[str] | None = None,
    strictness: InstrumentalStrictness = InstrumentalStrictness.STRICT,
    taste_affinity: float = 0.0,
    exploration: int = 40,
    min_popularity: int = 0,
    from_recommendations: bool = False,
    suitable_styles: list[str] | None = None,
    avoid_styles: list[str] | None = None,
    book_energy: float | None = None,
    atmospheres: list[str] | None = None,
    overall_mood: str | None = None,
    key_themes: list[str] | None = None,
    intimacy_vs_epic: float | None = None,
    narrative_voice: str | None = None,
    distinctive_signature: str | None = None,
    setting_texture: str | None = None,
    dominant_tones: list[str] | None = None,
    humor_level: float | None = None,
    realism_vs_dreaminess: float | None = None,
    anti_generic_notes: list[str] | None = None,
    vibe_keywords: list[str] | None = None,
) -> float:
    """
    Composite score ~0–100 with hard priority:

      1. Lyrics filter applied *before* scoring (caller)
      2. Book vibe & emotional tone (largest weight)
      3. User taste / comfort
      4. Light cinematic preference only when it fits the book
    """
    if seen_ids and track.id in seen_ids:
        return -1.0

    mode = lyrics.normalized()
    feats = track.features
    # Prefer energy target from book when available
    energy_target = book_energy if book_energy is not None else spec.energy
    fit_parts = [
        _feature_distance(feats.get("energy"), energy_target, 1.4),
        _feature_distance(feats.get("valence"), spec.valence, 1.1),
        _feature_distance(feats.get("acousticness"), spec.acousticness, 0.7),
        _feature_distance(feats.get("danceability"), spec.danceability, 0.4),
    ]
    if spec.tempo_bpm and feats.get("tempo"):
        tempo_fit = max(0.0, 1.0 - abs(feats["tempo"] - spec.tempo_bpm) / 60.0)
        fit_parts.append(tempo_fit)

    if mode is LyricsPreference.INSTRUMENTAL_ONLY:
        inst = feats.get("instrumentalness")
        if inst is not None:
            target = 0.75 if strictness <= InstrumentalStrictness.MODERATE else 0.55
            fit_parts.append(min(1.0, inst / target))
        elif is_likely_instrumental(track) is True:
            fit_parts.append(0.9)
        elif has_track_level_instrumental_signal(track):
            fit_parts.append(0.75)
        else:
            fit_parts.append(0.35)
    elif mode is LyricsPreference.PREFER_INSTRUMENTAL:
        inst = feats.get("instrumentalness")
        if inst is not None:
            fit_parts.append(0.35 + 0.65 * inst)
        elif is_likely_instrumental(track) is True:
            fit_parts.append(0.85)
        else:
            fit_parts.append(0.5)

    feature_fit = sum(fit_parts) / max(len(fit_parts), 1)

    popularity_known = track.popularity > 0
    if popularity_known:
        pop = track.popularity / 100.0
        pop_score = 0.25 + 0.75 * (math.log1p(pop * 12) / math.log1p(12))
        if min_popularity > 0 and track.popularity < min_popularity:
            pop_score *= 0.55
    else:
        pop_score = 0.55

    overlap = _keyword_overlap(spec, track)

    provenance = 0.75
    if has_track_level_instrumental_signal(track):
        provenance = 0.9
    if mode is LyricsPreference.ALLOW_LYRICS:
        provenance = 0.85 + 0.15 * overlap
    if from_recommendations:
        provenance = max(provenance, 0.88)

    diversity = 1.0
    if artist_counts and track.artists:
        primary = track.artists[0].lower()
        count = artist_counts.get(primary, 0)
        if count >= 2:
            diversity = max(0.25, 1.0 - 0.3 * (count - 1))

    dur_min = (track.duration_ms or 0) / 60000.0
    if dur_min < 0.6 or dur_min > 15:
        duration_factor = 0.45
    elif 1.2 <= dur_min <= 8:
        duration_factor = 1.0
    else:
        duration_factor = 0.85

    # Priority 2: book emotional world (dominant)
    style_mult = style_clash_score(track, suitable=suitable_styles, avoid=avoid_styles)
    vibe_mult = book_vibe_multiplier(
        track,
        book_energy=book_energy if book_energy is not None else spec.energy,
        atmospheres=atmospheres,
        overall_mood=overall_mood,
        key_themes=key_themes,
        intimacy_vs_epic=intimacy_vs_epic,
        narrative_voice=narrative_voice,
        distinctive_signature=distinctive_signature,
        setting_texture=setting_texture,
        dominant_tones=dominant_tones,
        humor_level=humor_level,
        realism_vs_dreaminess=realism_vs_dreaminess,
        anti_generic_notes=anti_generic_notes,
        vibe_keywords=vibe_keywords,
    )

    explore = max(0.0, min(1.0, exploration / 100.0))
    comfort = 1.0 - explore
    taste_score = taste_affinity
    novelty_score = 1.0 - taste_affinity

    # Light cinematic bonus only when book vibe allows it (not forced)
    cine = cinematic_fit(track)
    e = book_energy if book_energy is not None else (spec.energy or 0.5)
    intimacy = 0.5 if intimacy_vs_epic is None else float(intimacy_vs_epic)
    atms = {a.lower() for a in (atmospheres or [])}
    intimate = intimacy >= 0.6 or e <= 0.5 or bool(
        atms & {"intimate", "melancholic", "nostalgic", "hopeful", "playful", "romantic", "calm"}
    )
    if intimate and (e < 0.65 or intimacy >= 0.6):
        # Intimate books: only reward delicate cinematic, not epic
        cine_weight = 6.0 * (
            1.0
            if _INTIMATE_MARKERS.search(
                f"{track.name} {track.album} {' '.join(track.artists)}"
            )
            else 0.25
        )
    else:
        cine_weight = 10.0  # mild preference when epic/drama fits

    # Vibe-first weights
    if mode is LyricsPreference.INSTRUMENTAL_ONLY:
        feature_weight, pop_weight, prov_weight = 28.0, 10.0, 8.0
        vibe_overlap_weight = 16.0
        taste_weight = 0.0  # tops disabled
        novelty_weight = 6.0 * explore
    else:
        if feats and popularity_known:
            feature_weight, pop_weight, prov_weight = 26.0, 14.0, 8.0
        elif feats:
            feature_weight, pop_weight, prov_weight = 30.0, 10.0, 10.0
        else:
            feature_weight, pop_weight, prov_weight = 14.0, 12.0, 18.0
        vibe_overlap_weight = 14.0
        taste_weight = 28.0 * comfort + 6.0
        novelty_weight = 12.0 * explore
        if taste_affinity <= 0:
            feature_weight += taste_weight * 0.55
            pop_weight += taste_weight * 0.25
            novelty_weight += taste_weight * 0.2
            taste_weight = 0.0

    score = (
        feature_weight * feature_fit
        + pop_weight * pop_score
        + vibe_overlap_weight * overlap
        + prov_weight * provenance
        + 8.0 * diversity
        + taste_weight * taste_score
        + novelty_weight * novelty_score
        + cine_weight * cine
    ) * duration_factor * quality_penalty(track, popularity_known=popularity_known) * style_mult * vibe_mult

    if mode is LyricsPreference.INSTRUMENTAL_ONLY:
        likely = is_likely_instrumental(track)
        if likely is True:
            score += 10.0
        elif likely is False:
            score -= 30.0
        inst = feats.get("instrumentalness")
        if inst is not None:
            score += 14.0 * inst
        if inst is not None and inst < 0.7:
            score *= 0.8
    elif mode is LyricsPreference.PREFER_INSTRUMENTAL:
        if is_likely_instrumental(track) is True:
            score += 8.0
        inst = feats.get("instrumentalness")
        if inst is not None:
            score += 8.0 * inst

    if from_recommendations:
        score += 2.5 * (0.4 + 0.6 * comfort)

    return round(score, 3)


def passes_popularity_filter(
    track: RankedTrack,
    min_popularity: int,
    *,
    strict: bool = True,
) -> bool:
    """
    Popularity gate. When popularity is unknown (0/null from API), allow through
    so we don't empty the pool on restricted Spotify apps.
    """
    if min_popularity <= 0:
        return True
    if track.popularity <= 0:
        return not strict  # unknown: keep in soft mode
    return track.popularity >= min_popularity


def select_diverse(
    candidates: list[RankedTrack],
    n: int,
    *,
    max_per_artist: int = 2,
    min_score: float = 0.0,
) -> list[RankedTrack]:
    """Pick top-n tracks by score with artist diversity."""
    ordered = sorted(
        (t for t in candidates if t.score >= min_score),
        key=lambda t: t.score,
        reverse=True,
    )
    chosen: list[RankedTrack] = []
    artist_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    def norm(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", name.lower())

    for track in ordered:
        if len(chosen) >= n:
            break
        if track.id in seen_ids:
            continue
        nn = norm(track.name)
        if nn and nn in seen_names:
            continue
        primary = (track.artists[0] if track.artists else "").lower()
        if primary and artist_counts[primary] >= max_per_artist:
            continue
        chosen.append(track)
        seen_ids.add(track.id)
        if nn:
            seen_names.add(nn)
        if primary:
            artist_counts[primary] += 1

    # Relax artist cap
    if len(chosen) < n:
        for track in ordered:
            if len(chosen) >= n:
                break
            if track.id in seen_ids:
                continue
            nn = norm(track.name)
            if nn and nn in seen_names:
                continue
            chosen.append(track)
            seen_ids.add(track.id)
            if nn:
                seen_names.add(nn)

    return chosen


def total_duration_ms(tracks: list[RankedTrack]) -> int:
    return sum(t.duration_ms or 0 for t in tracks)
