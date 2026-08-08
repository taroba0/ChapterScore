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
    r"karaoke|radio\s*edit|official\s*video|music\s*video"
    r")\b",
    re.IGNORECASE,
)
_VOCAL_SOFT = re.compile(
    r"\b(feat\.|ft\.|featuring|remix|cover)\b",
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
    r"kiasmos|a winged victory for the sullen"
    r")",
    re.IGNORECASE,
)


def _artist_blob(track: RankedTrack) -> str:
    return " ".join(track.artists or [])


def is_likely_instrumental(track: RankedTrack) -> bool | None:
    """
    Ternary detector: True / False / None (unknown).

    When audio features are unavailable (common 403), rely on title, album,
    known score artists, and query provenance — never require features.
    """
    inst = track.features.get("instrumentalness")
    speech = track.features.get("speechiness")
    name = track.name or ""
    album = track.album or ""
    blob = f"{name} {album}"
    artists = _artist_blob(track)

    if inst is not None:
        if inst >= 0.65:
            return True
        if inst <= 0.20 and (speech is None or speech >= 0.04):
            return False
        if inst <= 0.35 and speech is not None and speech > 0.15:
            return False

    if _INSTRUMENTAL_CUES.search(blob):
        if inst is not None and inst < 0.15 and speech is not None and speech > 0.2:
            return False
        return True

    if _SCORE_ARTISTS.search(artists) or _SCORE_ARTISTS.search(album):
        # Known score/ambient composers — treat as instrumental unless title screams vocals
        if _VOCAL_HARD.search(name):
            return False
        return True

    if _VOCAL_HARD.search(name):
        return False

    return None


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
        factor *= 0.55
    # Only apply popularity penalties when the API actually returns values
    if popularity_known:
        if track.popularity <= 0:
            factor *= 0.45
        elif track.popularity < 10:
            factor *= 0.7
        elif track.popularity < 25:
            factor *= 0.85
    # Boost known score / ambient artists (critical when popularity is null)
    if _SCORE_ARTISTS.search(" ".join(track.artists or [])):
        factor *= 1.35
    # Album name cues for official soundtracks
    album = (track.album or "").lower()
    if any(k in album for k in ("soundtrack", "motion picture", "score", "ost")):
        factor *= 1.15
    return min(1.5, factor)


def passes_lyrics_filter(
    track: RankedTrack,
    lyrics: LyricsPreference,
    *,
    strictness: InstrumentalStrictness = InstrumentalStrictness.STRICT,
) -> bool:
    """
    Filter tracks by lyrics preference.

    For INSTRUMENTAL_ONLY, ``strictness`` controls how hard we filter.
    Title heuristics are secondary — missing audio features never hard-blocks
    at RELAXED/PERMISSIVE levels.
    """
    if is_undesirable(track):
        return False

    if lyrics != LyricsPreference.INSTRUMENTAL_ONLY:
        # Still drop pure karaoke junk for mixed / vocal modes
        return True

    inst = track.features.get("instrumentalness")
    speech = track.features.get("speechiness")
    likely = is_likely_instrumental(track)
    name = track.name or ""
    flavored = _query_is_instrumental_flavored(track.matched_query or "")

    # Always reject hard vocal markers in title at STRICT/MODERATE
    if strictness <= InstrumentalStrictness.MODERATE and _VOCAL_HARD.search(name):
        return False

    if strictness == InstrumentalStrictness.STRICT:
        if speech is not None and speech > 0.33:
            return False
        if likely is False:
            return False
        if inst is not None:
            return inst >= 0.55
        # No features (typical): title/artist cue OR soundtrack-flavored query
        return likely is True or flavored

    if strictness == InstrumentalStrictness.MODERATE:
        if speech is not None and speech > 0.45:
            return False
        if likely is False and (inst is None or inst < 0.4):
            return False
        if inst is not None:
            return inst >= 0.35 or likely is True
        # Accept soundtrack-query provenance, title cues, or soft vocal absence
        if likely is True or flavored:
            return True
        if _VOCAL_SOFT.search(name) and not _INSTRUMENTAL_CUES.search(name):
            return False
        # From an instrumental-oriented search with no hard vocal markers
        return flavored or likely is not False

    if strictness == InstrumentalStrictness.RELAXED:
        if speech is not None and speech > 0.55:
            return False
        if _VOCAL_HARD.search(name):
            return False
        if inst is not None and inst < 0.15 and (speech is None or speech > 0.08):
            return False
        if likely is False and inst is not None and inst < 0.25:
            return False
        # Without features: allow anything not clearly vocal
        return likely is not False

    # PERMISSIVE — almost everything except junk / karaoke
    if "karaoke" in name.lower():
        return False
    return True


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
) -> float:
    """
    Composite score ~0–100.

    Base signals: vibe fit, popularity, keywords, diversity.
    Personalization (when taste_affinity / exploration provided):
      - Comfort (low exploration) boosts tracks near the user's top artists
      - Exploration boosts unfamiliar artists and recommendation novelty
    """
    if seen_ids and track.id in seen_ids:
        return -1.0

    # Soft popularity gate (hard filter applied upstream; mild penalty here)
    if min_popularity > 0 and track.popularity > 0 and track.popularity < min_popularity:
        # Allow through with penalty rather than hard-drop (pool may be thin)
        pass

    feats = track.features
    fit_parts = [
        _feature_distance(feats.get("energy"), spec.energy, 1.2),
        _feature_distance(feats.get("valence"), spec.valence, 1.0),
        _feature_distance(feats.get("acousticness"), spec.acousticness, 0.6),
        _feature_distance(feats.get("danceability"), spec.danceability, 0.4),
    ]
    if spec.tempo_bpm and feats.get("tempo"):
        tempo_fit = max(0.0, 1.0 - abs(feats["tempo"] - spec.tempo_bpm) / 60.0)
        fit_parts.append(tempo_fit)

    if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
        inst = feats.get("instrumentalness")
        if inst is not None:
            target = 0.75 if strictness <= InstrumentalStrictness.MODERATE else 0.5
            fit_parts.append(min(1.0, inst / target))
        elif is_likely_instrumental(track) is True:
            fit_parts.append(0.85)
        elif _query_is_instrumental_flavored(track.matched_query or ""):
            fit_parts.append(0.65)
        else:
            fit_parts.append(0.45)
    elif lyrics == LyricsPreference.YES:
        inst = feats.get("instrumentalness")
        if inst is not None:
            fit_parts.append(1.0 - min(1.0, inst))

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

    provenance = 1.0 if _query_is_instrumental_flavored(track.matched_query or "") else 0.7
    if lyrics != LyricsPreference.INSTRUMENTAL_ONLY:
        provenance = 0.85 + 0.15 * overlap
    if from_recommendations:
        provenance = max(provenance, 0.9)

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

    # exploration ∈ [0,100] → comfort weight vs explore weight
    explore = max(0.0, min(1.0, exploration / 100.0))
    comfort = 1.0 - explore

    # Taste affinity 0–1; at high comfort, familiar artists score higher.
    # At high exploration, *lack* of affinity gets a small novelty bonus.
    taste_score = taste_affinity
    novelty_score = 1.0 - taste_affinity  # unfamiliar = high novelty

    # Base weights (before personalization mix)
    if feats and popularity_known:
        feature_weight, pop_weight, prov_weight = 30.0, 20.0, 10.0
    elif feats:
        feature_weight, pop_weight, prov_weight = 35.0, 10.0, 12.0
    elif popularity_known:
        feature_weight, pop_weight, prov_weight = 12.0, 28.0, 12.0
    else:
        feature_weight, pop_weight, prov_weight = 10.0, 10.0, 25.0

    # Personalization budget (~25 points) split by exploration slider
    taste_weight = 25.0 * comfort
    novelty_weight = 25.0 * explore
    # When no personalization data, redistribute to vibe/pop
    if taste_affinity <= 0 and explore < 0.05:
        feature_weight += 12.0
        pop_weight += 13.0
        taste_weight = 0.0
        novelty_weight = 0.0

    score = (
        feature_weight * feature_fit
        + pop_weight * pop_score
        + 12.0 * overlap
        + prov_weight * provenance
        + 12.0 * diversity
        + taste_weight * taste_score
        + novelty_weight * novelty_score
    ) * duration_factor * quality_penalty(track, popularity_known=popularity_known)

    if lyrics == LyricsPreference.INSTRUMENTAL_ONLY:
        likely = is_likely_instrumental(track)
        if likely is True:
            score += 8.0
        elif likely is False:
            score -= 12.0
        inst = feats.get("instrumentalness")
        if inst is not None:
            score += 10.0 * inst

    if lyrics == LyricsPreference.YES:
        if is_likely_instrumental(track) is True:
            score -= 5.0

    # Soft boost for recommendation-sourced tracks when recommendations preferred
    if from_recommendations:
        score += 3.0 * (0.5 + 0.5 * comfort)

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
