"""Domain models for books, vibe analysis, and track selection."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Mode(str, Enum):
    OVERALL = "overall"
    CHAPTER = "chapter"


class LyricsPreference(str, Enum):
    """
    Vocal / instrumental policy (highest-priority hard filter when strict).

    Values are CLI/API tokens; UI labels are friendlier.
    """

    ALLOW_LYRICS = "allow-lyrics"  # vocals fine
    PREFER_INSTRUMENTAL = "prefer-instrumental"  # soft bias toward instrumental
    INSTRUMENTAL_ONLY = "instrumental-only"  # hard: no clear vocals

    # Backward-compatible aliases (old CLI values)
    YES = "yes"  # legacy → treat as allow-lyrics
    NO = "no"  # legacy → treat as allow-lyrics

    def normalized(self) -> LyricsPreference:
        """Map legacy values onto the three canonical modes."""
        if self in (
            LyricsPreference.YES,
            LyricsPreference.NO,
            LyricsPreference.ALLOW_LYRICS,
        ):
            return LyricsPreference.ALLOW_LYRICS
        return self

    @property
    def is_instrumental_only(self) -> bool:
        return self.normalized() is LyricsPreference.INSTRUMENTAL_ONLY

    @property
    def prefers_instrumental(self) -> bool:
        n = self.normalized()
        return n in (
            LyricsPreference.PREFER_INSTRUMENTAL,
            LyricsPreference.INSTRUMENTAL_ONLY,
        )

    @property
    def display_label(self) -> str:
        return {
            LyricsPreference.ALLOW_LYRICS: "Allow lyrics",
            LyricsPreference.PREFER_INSTRUMENTAL: "Prefer instrumental",
            LyricsPreference.INSTRUMENTAL_ONLY: "Instrumental only",
            LyricsPreference.YES: "Allow lyrics",
            LyricsPreference.NO: "Allow lyrics",
        }.get(self, self.value)

    def effective_taste(self, taste: TasteStrength) -> TasteStrength:
        """Instrumental-only disables Top Artists (most tops are vocal acts)."""
        if self.is_instrumental_only:
            return TasteStrength.DISABLE
        return taste


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
    """User-controlled personalization for track selection.

    Priority (hard → soft):
      1. Lyrics / instrumental constraint
      2. Book vibe & musical style fit
      3. Exploration vs comfort
      4. Personal Top Artists (never overrides 1–2)
    """

    taste_strength: TasteStrength = TasteStrength.TOP_10
    use_recommendations: bool = True
    # 0 = max comfort (familiar artists), 100 = max exploration (new artists)
    # Default leans comfort (user wants familiarity); 0=comfort, 100=explore
    exploration: int = Field(default=25, ge=0, le=100)
    min_popularity: int = Field(default=30, ge=0, le=100)

    @property
    def comfort(self) -> float:
        """0–1 comfort weight (inverse of exploration)."""
        return 1.0 - (self.exploration / 100.0)

    @property
    def explore(self) -> float:
        """0–1 exploration weight."""
        return self.exploration / 100.0

    def effective_taste(self, lyrics: LyricsPreference) -> TasteStrength:
        """Top Artists disabled under instrumental-only (most tops are vocal acts)."""
        return lyrics.effective_taste(self.taste_strength)


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
    # Enriched public signals for deeper literary analysis
    publisher_blurb: str = ""
    reception_text: str = ""  # Wikipedia Reception / critical response
    themes_text: str = ""  # Themes / analysis / style sections
    review_snippets: list[str] = Field(default_factory=list)  # short public review language
    genre_labels: list[str] = Field(default_factory=list)  # coarse genre classifications
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

    def analysis_context_blob(self, *, max_chars: int = 18000) -> str:
        """Concatenate public signals for LLM analysis (truncated)."""
        parts: list[str] = []
        if self.description:
            parts.append(f"## Publisher / catalogue description\n{self.description}")
        if self.publisher_blurb and self.publisher_blurb != self.description:
            parts.append(f"## Publisher blurb\n{self.publisher_blurb}")
        if self.plot_summary:
            parts.append(f"## Plot / synopsis\n{self.plot_summary}")
        if self.reception_text:
            parts.append(f"## Critical reception & reader response language\n{self.reception_text}")
        if self.themes_text:
            parts.append(f"## Themes / style / analysis sections\n{self.themes_text}")
        if self.review_snippets:
            parts.append(
                "## Public review snippets (tone language)\n"
                + "\n".join(f"- {s}" for s in self.review_snippets[:12])
            )
        if self.subjects:
            parts.append("## Subjects / tags\n" + ", ".join(self.subjects[:40]))
        if self.genre_labels:
            parts.append("## Genre labels\n" + ", ".join(self.genre_labels[:20]))
        blob = "\n\n".join(parts)
        if len(blob) > max_chars:
            return blob[:max_chars] + "\n…[truncated]"
        return blob


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


class EmotionalAct(BaseModel):
    """Major structural beat when fine-grained chapters are unavailable or synthetic."""

    act_id: int | str = 1
    label: str = ""  # e.g. "Act I — arrival", "Turning point"
    mood: str = ""
    energy_level: float = Field(default=0.5, ge=0.0, le=1.0)
    atmospheres: list[str] = Field(default_factory=list)
    emotional_arc: str = ""
    pacing: str = "moderate"
    tone: str = ""
    vibe_note: str = ""
    search_queries: list[SearchQuerySpec] = Field(default_factory=list)


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

    # ── Expanded multi-dimensional literary profile ───────────────────────
    narrative_voice: str = ""  # wry, earnest, intimate, detached, sarcastic…
    writing_style: str = ""  # spare prose, lyrical, dialogue-heavy, experimental…
    dominant_tones: list[str] = Field(default_factory=list)
    secondary_tones: list[str] = Field(default_factory=list)
    humor_level: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="0=no humor, 1=comedy-forward",
    )
    sarcasm_irony_level: float = Field(default=0.2, ge=0.0, le=1.0)
    intimacy_vs_epic: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0=epic/sweeping scale, 1=intimate/personal scale",
    )
    realism_vs_dreaminess: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="0=gritty realism, 1=dreamy/surreal/magical",
    )
    setting_texture: str = ""  # time, place, social atmosphere
    sensory_atmosphere: str = ""  # light, weather, soundscape, body feeling
    pacing_profile: str = ""  # how pacing changes across the book
    distinctive_signature: str = Field(
        default="",
        description="What makes THIS book emotionally distinct from genre peers",
    )
    genre_peers_contrast: str = Field(
        default="",
        description="How it differs from typical books in the same genre",
    )
    anti_generic_notes: list[str] = Field(
        default_factory=list,
        description="Explicit warnings against collapsing into genre clichés",
    )

    chapters: list[ChapterVibe] = Field(default_factory=list)
    emotional_acts: list[EmotionalAct] = Field(
        default_factory=list,
        description="Major acts when chapter data is weak or synthetic",
    )
    overall_search_queries: list[SearchQuerySpec] = Field(default_factory=list)
    suggested_genres: list[str] = Field(default_factory=list)
    suitable_styles: list[str] = Field(
        default_factory=list,
        description="Musical styles that fit the book (e.g. dark ambient, orchestral, industrial)",
    )
    avoid_styles: list[str] = Field(
        default_factory=list,
        description="Styles that would clash (e.g. country, bubblegum pop, reggae)",
    )
    playlist_title_suggestion: str = ""
    playlist_description: str = ""

    @field_validator("overall_energy", "humor_level", "sarcasm_irony_level", "intimacy_vs_epic", "realism_vs_dreaminess", mode="before")
    @classmethod
    def clamp_01(cls, v: Any) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))

    def style_keywords_good(self) -> list[str]:
        """Lowercased tokens used for style matching."""
        raw = list(self.suitable_styles or []) + list(self.suggested_genres or [])
        return [s.strip().lower() for s in raw if s and s.strip()]

    def style_keywords_bad(self) -> list[str]:
        return [s.strip().lower() for s in (self.avoid_styles or []) if s and s.strip()]

    def vibe_keyword_pool(self) -> list[str]:
        """Rich token pool for ranking / query expansion."""
        pool: list[str] = []
        for part in (
            self.overall_mood,
            self.tone,
            self.narrative_voice,
            self.writing_style,
            self.era_feel,
            self.setting_texture,
            self.sensory_atmosphere,
            self.distinctive_signature,
            self.pacing_profile,
            self.emotional_arc,
        ):
            if part:
                pool.append(part)
        pool.extend(self.atmospheres or [])
        pool.extend(self.dominant_tones or [])
        pool.extend(self.secondary_tones or [])
        pool.extend(self.key_themes or [])
        pool.extend(self.anti_generic_notes or [])
        return pool


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
