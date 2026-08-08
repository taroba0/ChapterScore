"""Domain models for books, vibe analysis, and track selection."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Mode(str, Enum):
    OVERALL = "overall"
    CHAPTER = "chapter"


class LyricsPreference(str, Enum):
    YES = "yes"  # prefer songs with lyrics / vocals
    NO = "no"  # either is fine
    INSTRUMENTAL_ONLY = "instrumental-only"  # no vocals


class TasteStrength(str, Enum):
    """How many of the user's Spotify Top Artists to use as personal seeds."""

    DISABLE = "disable"
    TOP_5 = "top5"
    TOP_10 = "top10"
    TOP_15 = "top15"

    def artist_limit(self) -> int:
        return {
            TasteStrength.DISABLE: 0,
            TasteStrength.TOP_5: 5,
            TasteStrength.TOP_10: 10,
            TasteStrength.TOP_15: 15,
        }[self]


class PersonalizationPrefs(BaseModel):
    """User-controlled personalization for track selection."""

    taste_strength: TasteStrength = TasteStrength.TOP_10
    use_recommendations: bool = True
    # 0 = max comfort (familiar artists), 100 = max exploration (new artists)
    exploration: int = Field(default=40, ge=0, le=100)
    min_popularity: int = Field(default=28, ge=0, le=100)

    @property
    def comfort(self) -> float:
        """0–1 comfort weight (inverse of exploration)."""
        return 1.0 - (self.exploration / 100.0)

    @property
    def explore(self) -> float:
        """0–1 exploration weight."""
        return self.exploration / 100.0


class Atmosphere(str, Enum):
    CALM = "calm"
    TENSE = "tense"
    ROMANTIC = "romantic"
    EPIC = "epic"
    MELANCHOLIC = "melancholic"
    EERIE = "eerie"
    TRIUMPHANT = "triumphant"
    INTIMATE = "intimate"
    HOPEFUL = "hopeful"
    DARK = "dark"
    ADVENTUROUS = "adventurous"
    NOSTALGIC = "nostalgic"
    ANGRY = "angry"
    MYSTERIOUS = "mysterious"
    PLAYFUL = "playful"
    SOLEMN = "solemn"


# ── Book metadata ────────────────────────────────────────────────────────────


class ChapterSummary(BaseModel):
    number: int | str
    title: str | None = None
    summary: str = ""


class BookMetadata(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    isbn: str | None = None
    description: str = ""
    subjects: list[str] = Field(default_factory=list)
    publish_year: int | str | None = None
    cover_url: str | None = None
    page_count: int | None = None
    chapters: list[ChapterSummary] = Field(default_factory=list)
    plot_summary: str = ""
    source: str = ""
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @property
    def author_str(self) -> str:
        return ", ".join(self.authors) if self.authors else "Unknown"

    @property
    def display_name(self) -> str:
        if self.authors:
            return f"{self.title} by {self.author_str}"
        return self.title


# ── Vibe analysis (from Grok) ────────────────────────────────────────────────


class SearchQuerySpec(BaseModel):
    """A Spotify search query plus soft audio-feature targets."""

    query: str
    genres: list[str] = Field(default_factory=list)
    mood_keywords: list[str] = Field(default_factory=list)
    energy: float | None = Field(default=None, ge=0.0, le=1.0)
    valence: float | None = Field(default=None, ge=0.0, le=1.0)
    tempo_bpm: float | None = None
    instrumentalness_min: float | None = Field(default=None, ge=0.0, le=1.0)
    acousticness: float | None = Field(default=None, ge=0.0, le=1.0)
    danceability: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""


class ChapterVibe(BaseModel):
    chapter_number: int | str
    chapter_title: str | None = None
    mood: str
    energy_level: float = Field(ge=0.0, le=1.0, description="0=still, 1=intense")
    atmospheres: list[str] = Field(default_factory=list)
    emotional_arc: str = ""
    key_scenes: list[str] = Field(default_factory=list)
    pacing: str = "moderate"  # slow | moderate | fast
    tone: str = ""
    vibe_note: str = Field(
        default="",
        description="Short 1-sentence note for playlist description",
    )
    search_queries: list[SearchQuerySpec] = Field(default_factory=list)
    suggested_genres: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class BookVibeAnalysis(BaseModel):
    book_title: str
    authors: list[str] = Field(default_factory=list)
    overall_mood: str
    overall_energy: float = Field(ge=0.0, le=1.0)
    atmospheres: list[str] = Field(default_factory=list)
    emotional_arc: str = ""
    pacing: str = "moderate"
    tone: str = ""
    era_feel: str = ""  # e.g. "Victorian gothic", "modern noir", "epic fantasy"
    key_themes: list[str] = Field(default_factory=list)
    chapters: list[ChapterVibe] = Field(default_factory=list)
    overall_search_queries: list[SearchQuerySpec] = Field(default_factory=list)
    suggested_genres: list[str] = Field(default_factory=list)
    playlist_title_suggestion: str = ""
    playlist_description: str = ""

    @field_validator("overall_energy", mode="before")
    @classmethod
    def clamp_energy(cls, v: Any) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))


# ── Track selection ──────────────────────────────────────────────────────────


class RankedTrack(BaseModel):
    uri: str
    id: str
    name: str
    artists: list[str]
    album: str = ""
    popularity: int = 0  # 0 may mean "unknown" when Spotify omits the field
    duration_ms: int = 0
    explicit: bool = False
    preview_url: str | None = None
    external_url: str | None = None
    score: float = 0.0
    matched_query: str = ""
    chapter_number: int | str | None = None
    vibe_note: str = ""
    is_instrumental: bool | None = None
    features: dict[str, float] = Field(default_factory=dict)

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists)

    @property
    def display(self) -> str:
        return f"{self.name} — {self.artist_str}"


class PlaylistResult(BaseModel):
    id: str
    name: str
    url: str
    description: str = ""
    track_count: int = 0
    tracks: list[RankedTrack] = Field(default_factory=list)
    mode: Mode = Mode.OVERALL
    book_title: str = ""
