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
    r"brown\s*noise|pink\s*noise|sleep\s*sounds?|rain\s*sounds?|fan\s*noise|"
    r"royalty\s*free|copyright\s*free|no\s*copyright|ncs\b|"
    r"stock\s*music|background\s*music|youtube\s*audio|free\s*music|"
    r"energy\s*sound|ashamaluev|soundstripe|"
    r"test\s*tone|sine\s*wave|440\s*hz|hz\s*tone|silence\b"
    r")\b",
    re.IGNORECASE,
)

# Generic short titles — composition dedupe still keys on artist to avoid
# collapsing unrelated "Time" / "Theme" / "Prelude" pieces across composers.
_GENERIC_COMPOSITION_TITLES = frozenset(
    {
        "theme",
        "maintheme",
        "maintitle",
        "title",
        "titles",
        "time",
        "home",
        "love",
        "life",
        "dream",
        "hope",
        "end",
        "ending",
        "begin",
        "beginning",
        "intro",
        "opening",
        "closing",
        "credits",
        "prelude",
        "overture",
        "suite",
        "movement",
        "adagio",
        "allegro",
        "andante",
        "nocturne",
        "sonata",
        "interlude",
        "finale",
        "epilogue",
        "prologue",
        "untitled",
        "track",
        "song",
        "piece",
        "music",
    }
)

# Dead / non-listenable "music": single-key drones, scale exercises, tuning, etc.
# Prefer rejecting ambiguous practice tracks over including them.
_DEAD_OR_EXERCISE = re.compile(
    r"("
    # Title is essentially just a musical key (G minor, A major, Bb, C# minor…)
    r"(^|[\s\-–—:|/])([a-g](?:#|b|♯|♭|sharp|flat)?)\s*"
    r"(major|minor|maj|min|m)\b(\s*(scale|arpeggio|etude|étude|exercise))?|"
    r"\b([a-g](?:#|b|♯|♭)?)\s*(major|minor|maj|min)\s*(scale|arpeggio)s?\b|"
    r"\b(scale|scales|arpeggio|arpeggios|chromatic\s*scale)\b|"
    r"\b(tuning|drone|drones|ostinato|pedal\s*tone|held\s*note|single\s*note|one\s*note)\b|"
    r"\b(etude|étude|exercise|warm[\s-]?up|practice\s*piece|finger\s*exercise)\b|"
    r"\b(metronome|click\s*track|count[\s-]?in)\b|"
    r"\b(loop\s*pack|sample\s*pack|construction\s*kit)\b"
    r")",
    re.IGNORECASE,
)
# Whole-title key-only patterns: "G minor", "A Major", "Bb", "C#m"
_KEY_ONLY_TITLE = re.compile(
    r"^(?:the\s+)?"
    r"([a-g](?:#|b|♯|♭|sharp|flat)?)\s*"
    r"(major|minor|maj|min|m)?"
    r"(?:\s*(?:for\s+)?(?:orchestra|piano|strings|solo|ensemble))?$"
    ,
    re.IGNORECASE,
)

# Hard reject: podcasts, commentary, interviews, pure speech, audiobook clips
# Applies in ALL lyrics modes — prefer false negatives (reject music-adjacent talk).
_SPEECH_NON_MUSIC = re.compile(
    r"\b("
    r"podcast|pod\s*cast|"
    r"interview|interviews|"
    r"commentary|comment\s*track|audio\s*comment|"
    r"spoken[\s-]?word|spoken[\s-]?word\s*poetry|"
    r"audiobook|audio[\s-]?book|book\s*on\s*tape|"
    r"narrat(?:ed|ion|or)|as\s*read\s*by|read\s*by\b|"
    r"monologue|soliloquy|"
    r"lecture|sermon|speech\b|keynote|"
    r"talk\s*show|radio\s*show|radio\s*play|radio\s*drama|"
    r"full\s*episode|episode\s*#?\s*\d+|ep\.?\s*#?\s*\d+|"
    r"q\s*&\s*a|q\s*and\s*a|\bama\b|"
    r"book\s*club|author\s*talk|panel\s*discussion|"
    r"director'?s?\s*commentary|cast\s*commentary|"
    r"behind\s*the\s*scenes\s*interview|"
    r"true\s*crime\s*(podcast|episode)|"
    r"stand[\s-]?up\s*comedy|"  # pure talk performances
    r"guided\s*meditation\s*(talk|script)|"
    r"affirmations?\b|"
    r"sleep\s*story|bedtime\s*story\b|"
    r"teaser\s*trailer\s*(voice|narration)|"
    r"voice[\s-]?over\s*only|vo\s*only"
    r")\b",
    re.IGNORECASE,
)
# Album / show titles that almost always mean non-music catalogue
_SPEECH_ALBUM_SHOW = re.compile(
    r"\b("
    r"podcast|the\s+interview|spoken\s+word|audiobook|"
    r"full\s+cast\s+audio|bbc\s+radio\s+drama|"
    r"original\s+radio\s+broadcast"
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


def normalize_composition_title(name: str) -> str:
    """
    Aggressive title normalization for composition-level de-duplication.

    Strips punctuation, remaster/live/remix/version/mix/instrumental suffixes,
    and "from …" / "theme from …" movie-soundtrack tails so variants of the
    same work collapse to one key.
    """
    s = (name or "").lower()
    s = s.replace("&", " and ")
    # Drop parenthetical / bracketed edition tags
    s = re.sub(
        r"[\(\[\{][^\)\]\}]{0,80}[\)\]\}]",
        " ",
        s,
    )
    # "From Interstellar / From the Motion Picture …"
    s = re.sub(
        r"\b(from|theme\s+from|taken\s+from)\s+(the\s+)?(motion\s+picture|movie|film|series|show|game)\b.*$",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"\bfrom\s+[\"'].+?[\"']\s*$", " ", s, flags=re.I)
    s = re.sub(r"\bfrom\s+[a-z0-9][a-z0-9\s:&'\-]{1,40}$", " ", s, flags=re.I)
    # Common variant suffixes (not core title words like "theme" alone)
    s = re.sub(
        r"\b("
        r"remaster(ed)?(\s*\d{4})?|live(\s+at|\s+in|\s+from)?|"
        r"radio\s*edit|extended(\s+mix)?|deluxe|bonus(\s+track)?|"
        r"instrumental(\s+version)?|karaoke|acoustic(\s+version)?|"
        r"piano\s+version|orchestral\s+version|film\s+version|"
        r"(original\s+)?(version|remix|mix|edit)|cover|tribute|"
        r"original\s+(motion\s+picture\s+)?soundtrack|"
        r"ost\b|movie\s+soundtrack|soundtrack\s+version|"
        r"end\s+credits(\s+suite)?"
        r")\b",
        " ",
        s,
        flags=re.I,
    )
    # Trailing " - Something" edition markers
    s = re.sub(
        r"\s*[-–—:|/]+\s*(remaster|live|remix|mix|edit|version|instrumental|from)\b.*$",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def is_undesirable(track: RankedTrack) -> bool:
    blob = f"{track.name or ''} {track.album or ''} {' '.join(track.artists or [])}"
    if _UNDESIRABLE.search(blob):
        return True
    return False


def is_dead_or_exercise_track(track: RankedTrack) -> bool:
    """
    HARD reject: non-listenable / practice / empty 'music'.

    Catches single-key drones ("G minor"), scale exercises, tuning tones,
    ostinato loops, and similar catalogue noise. Prefer reject when unsure.
    """
    name = (track.name or "").strip()
    album = track.album or ""
    artists = _artist_blob(track)
    blob = f"{name} {album}"

    if not name:
        return True

    # Whole title is just a key / key + "for orchestra"
    if _KEY_ONLY_TITLE.match(name):
        return True

    if _DEAD_OR_EXERCISE.search(name):
        # Allow real pieces that merely mention a key in a longer artistic title
        # e.g. "Moonlight Sonata in C# Minor" — keep if it has a substantial name head
        head = re.split(r"\bin\s+[a-g]", name, maxsplit=1, flags=re.I)[0].strip()
        artistic = bool(
            re.search(
                r"\b("
                r"sonata|concerto|symphony|nocturne|prelude|fugue|rhapsody|"
                r"ballade|impromptu|scherzo|waltz|suite|overture|requiem|"
                r"theme|fantasia|capriccio|intermezzo|romance"
                r")\b",
                head,
                re.I,
            )
        )
        # "G minor" alone / "Scale in G" / "Drone" → reject
        # "Piano Sonata in G minor" → keep
        if not artistic or len(re.sub(r"[^a-z0-9]", "", head.lower())) < 6:
            return True

    # Album cues for exercise packs
    if re.search(
        r"\b(scales?\s*(and|&)?\s*arpeggios?|practice\s*tracks?|tuning\s*notes?|"
        r"orchestra\s*tuning|warm[\s-]?ups?)\b",
        album,
        re.I,
    ):
        return True

    # Extremely empty features: near-zero energy + near-zero valence + long duration
    # often indicates a held drone / silence bed
    energy = track.features.get("energy")
    valence = track.features.get("valence")
    dur_ms = track.duration_ms or 0
    if (
        energy is not None
        and valence is not None
        and energy < 0.08
        and valence < 0.12
        and dur_ms >= 180_000
        and not _SCORE_ARTISTS.search(artists)
        and not _INSTRUMENTAL_CUES.search(name)
    ):
        # Title also looks sparse / technical
        if len(normalize_composition_title(name)) <= 8:
            return True

    return False


def is_speech_or_non_music(track: RankedTrack) -> bool:
    """
    HARD content filter: True if the item is primarily speech / non-music.

    Rejects podcasts, interviews, commentary, audiobook-style narration,
    monologues, and high-speechiness audio. Prefer rejecting ambiguous talk
    over letting speech into the playlist. Applies in every lyrics mode.
    """
    name = track.name or ""
    album = track.album or ""
    artists = _artist_blob(track)
    blob = f"{name} {album} {artists}"
    mq = (track.matched_query or "").lower()

    # Lexical cues on title / album / artist
    if _SPEECH_NON_MUSIC.search(blob):
        return True
    if _SPEECH_ALBUM_SHOW.search(album) or _SPEECH_ALBUM_SHOW.search(name):
        return True

    # Podcast-ish artist names (e.g. "Something Podcast")
    if re.search(r"\bpodcasts?\b", artists, re.I):
        return True

    # Search provenance was explicitly non-music (rare but defensive)
    if any(
        k in mq
        for k in (
            "podcast",
            "interview",
            "audiobook",
            "spoken word",
            "commentary episode",
        )
    ):
        # Only reject if the track itself also lacks strong music cues
        if not _INSTRUMENTAL_CUES.search(blob) and not _SCORE_ARTISTS.search(artists):
            return True

    speech = track.features.get("speechiness")
    inst = track.features.get("instrumentalness")
    # Spotify: >0.66 ≈ entirely spoken; 0.33–0.66 mixed speech/music
    if speech is not None:
        if speech >= 0.55:
            return True  # primarily talking
        if speech >= 0.40 and (inst is None or inst < 0.35):
            return True
        if speech >= 0.33 and (inst is not None and inst < 0.15):
            # High speech + almost no instrumental content
            if not _INSTRUMENTAL_CUES.search(blob) and not _SCORE_ARTISTS.search(artists):
                return True

    return False


def passes_content_filter(track: RankedTrack) -> bool:
    """
    Universal hard gate: real, listenable, reading-safe music only.

    Blocks speech/podcast/commentary, junk, dead-note / exercise tracks,
    and blasting trailer-level intensity. Prefer reject when unsure.
    """
    if is_speech_or_non_music(track):
        return False
    if is_undesirable(track):
        return False
    if is_dead_or_exercise_track(track):
        return False
    if is_too_intense_for_reading(track):
        return False
    return True


def quality_penalty(track: RankedTrack, *, popularity_known: bool = True) -> float:
    """Return a multiplicative score factor for catalogue quality signals."""
    factor = 1.0
    blob = f"{track.name or ''} {' '.join(track.artists or [])}"
    if _LOW_QUALITY.search(blob):
        factor *= 0.45
    if popularity_known:
        if track.popularity <= 0:
            factor *= 0.5
        elif track.popularity < 15:
            factor *= 0.55
        elif track.popularity < 25:
            factor *= 0.7
        elif track.popularity < 35:
            factor *= 0.85
        elif track.popularity >= 55:
            factor *= 1.08
    # Mild quality cues only — book vibe multiplier decides epic vs intimate fit
    if _SCORE_ARTISTS.search(" ".join(track.artists or [])):
        factor *= 1.12
    album = track.album or ""
    if _CINEMATIC_ALBUM.search(album):
        factor *= 1.1
    elif any(k in album.lower() for k in ("soundtrack", "motion picture", "score", "ost")):
        factor *= 1.08
    return min(1.65, factor)


def passes_lyrics_filter(
    track: RankedTrack,
    lyrics: LyricsPreference,
    *,
    strictness: InstrumentalStrictness = InstrumentalStrictness.STRICT,
) -> bool:
    """
    Priority-1 HARD filters:
      0. Content: no podcasts / speech / commentary (all modes)
      1. Lyrics / instrumental policy

    INSTRUMENTAL_ONLY is intentionally harsh: prefer empty pool over vocals.
    Track-level evidence is required — search-query wording is never enough.
    """
    # Universal: never admit speech / podcast / non-music
    if not passes_content_filter(track):
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
        # Sung vocals OK; pure speech already blocked above
        return True

    if mode is LyricsPreference.PREFER_INSTRUMENTAL:
        if "karaoke" in name.lower():
            return False
        # Soft bias: still block very high speech even if features are partial
        if speech is not None and speech > 0.45:
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

# Too loud / aggressive for reading focus (hard preference to reject)
_READING_TOO_INTENSE = re.compile(
    r"("
    r"two steps from hell|thomas bergersen|audiomachine|immediate music|"
    r"trailer\s*music|hybrid\s*trailer|epic\s*battle|war\s*drums|"
    r"brass\s*fanfare|scream(ing)?|death\s*metal|black\s*metal|"
    r"dubstep\s*drop|festival\s*drop|brostep|"
    r"aggressive|crushing|brutal|chaos|chaotic|"
    r"transformers|avengers|man of steel|gladiator\s*main"
    r")",
    re.IGNORECASE,
)


def reading_safe_energy_target(
    book_energy: float | None,
    *,
    intimacy_vs_epic: float | None = None,
    target_max: float | None = None,
) -> float:
    """
    Map literary book energy into a reading-safe musical energy target.

    High-energy books become taut / expectant, not blasting. Comfortable
    continuity for focus beats matching peak plot intensity.
    """
    e = 0.5 if book_energy is None else max(0.0, min(1.0, float(book_energy)))
    intimacy = 0.5 if intimacy_vs_epic is None else max(0.0, min(1.0, float(intimacy_vs_epic)))
    # Soft curve: compress the high end into a readable band
    compressed = 0.16 + 0.42 * (e**0.9)
    try:
        from chapterscore.config import get_settings

        tmax = target_max if target_max is not None else get_settings().chapterscore_reading_energy_target_max
    except Exception:
        tmax = target_max if target_max is not None else 0.56
    if intimacy >= 0.7:
        compressed = min(compressed, 0.46)
    elif intimacy >= 0.55:
        compressed = min(compressed, 0.52)
    return float(min(tmax, max(0.14, compressed)))


def reading_energy_ceiling() -> float:
    try:
        from chapterscore.config import get_settings

        return float(get_settings().chapterscore_reading_energy_ceiling)
    except Exception:
        return 0.68


def track_energy_estimate(track: RankedTrack) -> float:
    """Spotify energy feature, or a lexical heuristic when features are missing."""
    e = track.features.get("energy")
    if e is not None:
        return float(e)
    blob = f"{track.name or ''} {track.album or ''} {' '.join(track.artists or [])}".lower()
    if _READING_TOO_INTENSE.search(blob) or _EPIC_TRAILER_MARKERS.search(blob):
        return 0.78
    if _INTIMATE_MARKERS.search(blob):
        return 0.28
    if _DREAMY_MARKERS.search(blob):
        return 0.32
    if _PLAYFUL_MARKERS.search(blob):
        return 0.48
    return 0.45


def is_too_intense_for_reading(track: RankedTrack) -> bool:
    """
    Reading-safe hard-ish gate: reject blasting / trailer / scream-level tracks.

    Prefer texture over volume. Book tension is allowed; chaos is not.
    """
    blob = f"{track.name or ''} {track.album or ''} {' '.join(track.artists or [])}".lower()
    if _READING_TOO_INTENSE.search(blob):
        return True
    energy = track.features.get("energy")
    loudness_proxy = track.features.get("loudness")  # often missing; dB when present
    ceiling = reading_energy_ceiling()
    if energy is not None and float(energy) > ceiling:
        return True
    # Very high energy + low acousticness ≈ dense/noisy production
    acoustic = track.features.get("acousticness")
    if energy is not None and acoustic is not None:
        if float(energy) > ceiling - 0.05 and float(acoustic) < 0.12:
            return True
    if loudness_proxy is not None and float(loudness_proxy) > -5.5 and (
        energy is None or float(energy) > 0.55
    ):
        return True
    return False


def reading_energy_fit(
    track_energy: float | None,
    target: float,
    *,
    ceiling: float | None = None,
) -> float:
    """
    Asymmetric fit: overshooting the reading target hurts more than being calmer.

    Returns a 0–1 factor for scoring.
    """
    if track_energy is None:
        return 0.7
    te = float(track_energy)
    ceil = ceiling if ceiling is not None else reading_energy_ceiling()
    if te > ceil:
        return 0.12
    if te > target:
        gap = te - target
        return max(0.28, 1.0 - 2.0 * gap)
    gap = target - te
    return max(0.5, 1.0 - 0.85 * gap)


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
    era_feel: str | None = None,
    pacing: str | None = None,
) -> float:
    """
    Multiplier ~[0.15, 1.5] for reading-companion book fit.

    Atmosphere-first: setting, era, intended emotion, and pacing outweigh raw
    plot-intensity matching. High-energy books stay taut, never trailer-loud.
    """
    energy = 0.5 if book_energy is None else float(book_energy)
    intimacy = 0.5 if intimacy_vs_epic is None else float(intimacy_vs_epic)
    humor = 0.3 if humor_level is None else float(humor_level)
    dream = 0.4 if realism_vs_dreaminess is None else float(realism_vs_dreaminess)
    safe_target = reading_safe_energy_target(energy, intimacy_vs_epic=intimacy)
    atms = {a.lower() for a in (atmospheres or [])}
    tones = {t.lower() for t in (dominant_tones or [])}
    mood = (overall_mood or "").lower()
    themes = " ".join(key_themes or []).lower()
    voice = (narrative_voice or "").lower()
    signature = (distinctive_signature or "").lower()
    setting = (setting_texture or "").lower()
    era = (era_feel or "").lower()
    pace = (pacing or "").lower()
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
    # "Epic world" ≠ permission for trailer volume when reading
    epic_world = (
        intimacy <= 0.35
        or bool(atms & {"epic", "adventurous", "mythic"})
        or any(k in setting or k in era for k in ("myth", "empire", "quest", "battlefield"))
    ) and intimacy < 0.55
    blocks_epic = any(
        k in anti for k in ("not epic", "no epic", "not trailer", "no trailer", "not battle")
    )
    if blocks_epic:
        intimate_book = True
        epic_world = False

    # Atmosphere-first token pools (heavier weight than generic themes)
    def _tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3}

    stop = {
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
        "reading",
        "reader",
    }
    setting_tokens = (_tokens(setting) | _tokens(era)) - stop
    emotion_tokens = (
        _tokens(mood) | tones | {a for a in atms if len(a) > 3} | _tokens(signature)
    ) - stop
    theme_tokens = _tokens(themes) - stop
    voice_tokens = _tokens(voice) - stop
    extra = set()
    for item in vibe_keywords or []:
        extra |= _tokens(str(item))
    extra -= stop

    setting_hits = sum(1 for t in setting_tokens if t in blob)
    emotion_hits = sum(1 for t in emotion_tokens if t in blob)
    theme_hits = sum(1 for t in theme_tokens if t in blob)
    voice_hits = sum(1 for t in voice_tokens if t in blob)
    extra_hits = sum(1 for t in extra if t in blob)

    if setting_hits:
        mult *= min(1.35, 1.0 + 0.10 * setting_hits)  # strongest
    if emotion_hits:
        mult *= min(1.32, 1.0 + 0.08 * emotion_hits)
    if voice_hits:
        mult *= min(1.18, 1.0 + 0.05 * voice_hits)
    if theme_hits:
        mult *= min(1.15, 1.0 + 0.04 * theme_hits)
    if extra_hits:
        mult *= min(1.12, 1.0 + 0.03 * extra_hits)

    # Pacing texture cues
    if pace:
        if "slow" in pace and any(k in blob for k in ("ambient", "drone", "piano", "quiet", "still")):
            mult *= 1.08
        if "fast" in pace or "propulsive" in pace:
            if any(k in blob for k in ("pulse", "driving", "tense", "ostinato", "motorik")):
                mult *= 1.06
            if _EPIC_TRAILER_MARKERS.search(blob):
                mult *= 0.7  # propulsion ≠ trailer blast

    # Reading-safe energy fit (asymmetric — loud overshoot hurts)
    t_energy = track.features.get("energy")
    e_fit = reading_energy_fit(t_energy if t_energy is not None else track_energy_estimate(track), safe_target)
    mult *= 0.55 + 0.55 * e_fit

    # Universal reading companion: never reward trailer/battle music much
    if _EPIC_TRAILER_MARKERS.search(blob) or _READING_TOO_INTENSE.search(blob):
        mult *= 0.22 if intimate_book else 0.35
    if intimate_book:
        if re.search(
            r"hans zimmer|two steps|john williams|howard shore",
            blob,
            re.I,
        ) and not _INTIMATE_MARKERS.search(blob):
            if re.search(
                r"battle|pirates|gladiator|dark knight|inception\s*main",
                blob,
                re.I,
            ):
                mult *= 0.35
            else:
                mult *= 0.65
        if _INTIMATE_MARKERS.search(blob):
            mult *= 1.22 if intimacy >= 0.65 else 1.15

    # Mythic / historical / fantasy worlds: texture over volume
    world_blob = f"{setting} {era} {mood} {themes}"
    if any(k in world_blob for k in ("myth", "ancient", "historical", "fantasy", "folklore", "ritual")):
        if any(k in blob for k in ("folk", "lyre", "chant", "choir", "ancient", "modal", "harp", "lute")):
            mult *= 1.12
        if _EPIC_TRAILER_MARKERS.search(blob):
            mult *= 0.55
    if any(k in world_blob for k in ("realist", "contemporary", "domestic", "everyday")):
        if _EPIC_TRAILER_MARKERS.search(blob):
            mult *= 0.4
        if any(k in blob for k in ("indie", "piano", "acoustic", "chamber", "neoclassical")):
            mult *= 1.08

    # Even "epic world" books: modest lift for adventurous texture, not trailer banks
    if epic_world and not intimate_book:
        if any(k in blob for k in ("adventure", "explore", "journey", "horizon", "wind")):
            mult *= 1.08
        if _EPIC_TRAILER_MARKERS.search(blob):
            mult *= 0.85  # was a boost — now a soft penalty for reading

    if humor >= 0.55:
        if _PLAYFUL_MARKERS.search(blob):
            mult *= 1.18
        if _EPIC_TRAILER_MARKERS.search(blob):
            mult *= 0.5

    if dream >= 0.6:
        if _DREAMY_MARKERS.search(blob):
            mult *= 1.15
        if _EPIC_TRAILER_MARKERS.search(blob):
            mult *= 0.55

    return max(0.15, min(1.5, mult))


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
    era_feel: str | None = None,
    pacing: str | None = None,
) -> float:
    """
    Composite score ~0–100 for a *reading companion* playlist:

      1. Lyrics / content filters applied before scoring (caller)
      2. Atmosphere / setting / emotion fit (dominant)
      3. Reading-safe energy (not peak plot intensity)
      4. Catalogue quality
      5. Soft taste / comfort (never overrides 1–4)
    """
    if seen_ids and track.id in seen_ids:
        return -1.0

    mode = lyrics.normalized()
    feats = track.features
    # Reading-safe energy target (compresses high book energy into a focus-friendly band)
    energy_target = reading_safe_energy_target(
        book_energy if book_energy is not None else spec.energy,
        intimacy_vs_epic=intimacy_vs_epic,
    )
    fit_parts = [
        # Softer weight on raw energy distance — atmosphere matters more
        _feature_distance(feats.get("energy"), energy_target, 0.9),
        _feature_distance(feats.get("valence"), spec.valence, 1.0),
        _feature_distance(feats.get("acousticness"), spec.acousticness, 0.85),
        _feature_distance(feats.get("danceability"), spec.danceability, 0.35),
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

    # Atmosphere / setting / emotion (dominant for reading companion)
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
        era_feel=era_feel,
        pacing=pacing,
    )

    explore = max(0.0, min(1.0, exploration / 100.0))
    comfort = 1.0 - explore
    taste_score = taste_affinity
    novelty_score = 1.0 - taste_affinity

    # Cinematic bonus only for delicate / atmospheric cues — never trailer banks
    cine = cinematic_fit(track)
    intimacy = 0.5 if intimacy_vs_epic is None else float(intimacy_vs_epic)
    blob = f"{track.name} {track.album} {' '.join(track.artists)}"
    if _READING_TOO_INTENSE.search(blob) or _EPIC_TRAILER_MARKERS.search(blob):
        cine_weight = 0.5
        cine *= 0.2
    elif _INTIMATE_MARKERS.search(blob) or _DREAMY_MARKERS.search(blob):
        cine_weight = 7.0
    else:
        cine_weight = 3.5  # mild; atmosphere > cinema prestige

    # Reading-safe energy factor (extra to feature_fit)
    e_safe = reading_energy_fit(
        feats.get("energy") if feats.get("energy") is not None else track_energy_estimate(track),
        energy_target,
    )

    # Atmosphere-first weights; taste is softest
    if mode is LyricsPreference.INSTRUMENTAL_ONLY:
        feature_weight, pop_weight, prov_weight = 22.0, 10.0, 8.0
        vibe_overlap_weight = 20.0
        taste_weight = (10.0 * comfort + 3.0) if taste_affinity > 0 else 0.0
        novelty_weight = 5.0 * explore
    else:
        if feats and popularity_known:
            feature_weight, pop_weight, prov_weight = 20.0, 12.0, 8.0
        elif feats:
            feature_weight, pop_weight, prov_weight = 24.0, 10.0, 10.0
        else:
            feature_weight, pop_weight, prov_weight = 12.0, 12.0, 16.0
        vibe_overlap_weight = 18.0
        taste_weight = 18.0 * comfort + 4.0
        novelty_weight = 10.0 * explore
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
        + 14.0 * e_safe  # reading continuity / safe intensity
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


def _norm_title(name: str) -> str:
    """Backward-compatible alias for composition title normalization."""
    return normalize_composition_title(name)


def _primary_artist_key(track: RankedTrack) -> str:
    return re.sub(r"[^a-z0-9]+", "", (track.artists[0] if track.artists else "").lower())


def composition_key(track: RankedTrack) -> str:
    """
    Identity for the same *composition* / work across recordings.

    Same normalized title (after stripping remaster/live/from-movie/instrumental
    suffixes) is treated as one work — even across different artists — unless
    the title is too generic (then artist is included to avoid false merges).
    """
    nt = normalize_composition_title(track.name)
    if not nt:
        return f"id:{track.id}" if track.id else "empty"
    if len(nt) < 5 or nt in _GENERIC_COMPOSITION_TITLES:
        return f"gen:{_primary_artist_key(track)}|{nt}"
    return f"comp:{nt}"


def recording_key(track: RankedTrack) -> str:
    """Same as composition_key (kept for older imports/tests)."""
    return composition_key(track)


def _track_quality_tuple(track: RankedTrack) -> tuple:
    """Higher is better — used when keeping the best of duplicate compositions."""
    return (
        float(track.score or 0.0),
        int(track.popularity or 0),
        1 if _SCORE_ARTISTS.search(_artist_blob(track)) else 0,
        -(track.duration_ms or 0),  # mild preference for not-absurdly-long beds
    )


def dedupe_tracks(tracks: list[RankedTrack]) -> list[RankedTrack]:
    """
    Strict final pass: one track per Spotify id and per composition.

    When multiple recordings of the same work appear, keep the best by
    score → popularity → known score-artist legitimacy.
    """
    best_by_id: dict[str, RankedTrack] = {}
    no_id: list[RankedTrack] = []
    for t in tracks:
        if t.id:
            prev = best_by_id.get(t.id)
            if prev is None or _track_quality_tuple(t) > _track_quality_tuple(prev):
                best_by_id[t.id] = t
        else:
            no_id.append(t)
    pool = list(best_by_id.values()) + no_id

    best_by_comp: dict[str, RankedTrack] = {}
    for t in pool:
        key = composition_key(t)
        prev = best_by_comp.get(key)
        if prev is None or _track_quality_tuple(t) > _track_quality_tuple(prev):
            best_by_comp[key] = t

    # Preserve first-seen playlist order of winning compositions
    seen_keys: set[str] = set()
    out: list[RankedTrack] = []
    for t in tracks:
        key = composition_key(t)
        winner = best_by_comp.get(key)
        if winner is None or key in seen_keys:
            continue
        if winner is t or (t.id and winner.id == t.id):
            out.append(winner)
            seen_keys.add(key)
    if len(out) < len(best_by_comp):
        for key, winner in best_by_comp.items():
            if key not in seen_keys:
                out.append(winner)
                seen_keys.add(key)
    return out


def filter_music_only(tracks: list[RankedTrack]) -> list[RankedTrack]:
    """Final safety net: drop speech / junk / dead-note tracks."""
    return [t for t in tracks if passes_content_filter(t)]


def select_diverse(
    candidates: list[RankedTrack],
    n: int,
    *,
    max_per_artist: int = 2,
    min_score: float = 0.0,
) -> list[RankedTrack]:
    """Pick up to n tracks by score with artist diversity and composition de-dupe."""
    # Pre-collapse duplicate compositions, keeping the better scoring variant
    candidates = dedupe_tracks([t for t in candidates if t.score >= min_score])
    ordered = sorted(candidates, key=_track_quality_tuple, reverse=True)
    chosen: list[RankedTrack] = []
    artist_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    seen_compositions: set[str] = set()

    def _take(track: RankedTrack, *, respect_artist_cap: bool) -> bool:
        if track.id and track.id in seen_ids:
            return False
        ck = composition_key(track)
        if ck in seen_compositions:
            return False
        primary = (track.artists[0] if track.artists else "").lower()
        if respect_artist_cap and primary and artist_counts[primary] >= max_per_artist:
            return False
        chosen.append(track)
        if track.id:
            seen_ids.add(track.id)
        seen_compositions.add(ck)
        if primary:
            artist_counts[primary] += 1
        return True

    for track in ordered:
        if len(chosen) >= n:
            break
        _take(track, respect_artist_cap=True)

    # Relax artist cap only — never relax duplicate / composition rules
    if len(chosen) < n:
        for track in ordered:
            if len(chosen) >= n:
                break
            _take(track, respect_artist_cap=False)

    return dedupe_tracks(chosen)


def apply_overall_cohesion(
    tracks: list[RankedTrack],
    *,
    book_energy: float | None,
    intimacy_vs_epic: float | None = None,
    max_energy_gap: float = 0.28,
) -> list[RankedTrack]:
    """
    Soft-penalize tracks far from the *reading-safe* energy target so overall
    mode stays one emotional world (shuffle-friendly, focus-friendly).
    """
    target = reading_safe_energy_target(book_energy, intimacy_vs_epic=intimacy_vs_epic)
    ceiling = reading_energy_ceiling()
    adjusted: list[RankedTrack] = []
    for t in tracks:
        te = track_energy_estimate(t)
        if te > ceiling:
            t.score = round(t.score * 0.2, 3)
        else:
            fit = reading_energy_fit(te, target, ceiling=ceiling)
            # Pull scores toward reading-safe center
            t.score = round(t.score * (0.55 + 0.55 * fit), 3)
            gap = abs(te - target)
            if gap > max_energy_gap:
                t.score = round(t.score * max(0.35, 1.0 - 1.3 * (gap - max_energy_gap)), 3)
        adjusted.append(t)
    return adjusted


def smooth_playlist_order(
    tracks: list[RankedTrack],
    *,
    max_jump: float | None = None,
    drop_jarring: bool = True,
) -> list[RankedTrack]:
    """
    Reorder tracks so adjacent energy/intensity jumps stay small.

    Greedy nearest-neighbor from a mid-energy seed. Prefer dropping a leftover
    jarring track over inserting a soft→violent shock. Reading continuity first.
    """
    if len(tracks) <= 2:
        return list(tracks)
    try:
        from chapterscore.config import get_settings

        jump = (
            max_jump
            if max_jump is not None
            else float(get_settings().chapterscore_max_adjacent_energy_jump)
        )
    except Exception:
        jump = max_jump if max_jump is not None else 0.20

    remaining = list(tracks)
    remaining.sort(key=track_energy_estimate)
    # Start near the median energy for cohesion under shuffle too
    start_idx = len(remaining) // 2
    ordered: list[RankedTrack] = [remaining.pop(start_idx)]

    while remaining:
        last_e = track_energy_estimate(ordered[-1])
        within = [
            (i, t)
            for i, t in enumerate(remaining)
            if abs(track_energy_estimate(t) - last_e) <= jump
        ]
        if within:
            # Among smooth neighbors, prefer higher score then closer energy
            i, _ = max(
                within,
                key=lambda it: (
                    float(it[1].score or 0.0),
                    -abs(track_energy_estimate(it[1]) - last_e),
                ),
            )
            ordered.append(remaining.pop(i))
            continue

        # No smooth neighbor — take closest
        i, closest = min(
            enumerate(remaining),
            key=lambda it: abs(track_energy_estimate(it[1]) - last_e),
        )
        delta = abs(track_energy_estimate(closest) - last_e)
        if drop_jarring and delta > jump * 1.65:
            # Prefer fewer continuous tracks over a focus-breaking jump
            remaining.pop(i)
            continue
        ordered.append(remaining.pop(i))

    return ordered


def smooth_chapter_playlist(
    tracks: list[RankedTrack],
    *,
    max_jump: float | None = None,
) -> list[RankedTrack]:
    """
    Smooth within each chapter block, then soft-bridge between chapters.

    Progression across chapters is preserved; soft→violent jumps are not.
    """
    if not tracks:
        return []
    from collections import OrderedDict

    groups: OrderedDict[str | int | None, list[RankedTrack]] = OrderedDict()
    for t in tracks:
        groups.setdefault(t.chapter_number, []).append(t)

    out: list[RankedTrack] = []
    prev_e: float | None = None
    for _ch, group in groups.items():
        smoothed = smooth_playlist_order(group, max_jump=max_jump, drop_jarring=True)
        if prev_e is not None and len(smoothed) > 1:
            # Rotate so the block opens closest to the previous chapter's ending energy
            best_i = min(
                range(len(smoothed)),
                key=lambda i: abs(track_energy_estimate(smoothed[i]) - prev_e),
            )
            smoothed = smoothed[best_i:] + smoothed[:best_i]
            # Re-smooth the rotated list lightly for internal continuity
            smoothed = smooth_playlist_order(smoothed, max_jump=max_jump, drop_jarring=False)
        out.extend(smoothed)
        if smoothed:
            prev_e = track_energy_estimate(smoothed[-1])
    return out


def total_duration_ms(tracks: list[RankedTrack]) -> int:
    return sum(t.duration_ms or 0 for t in tracks)
