"""Configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_NAME = "chapterscore"
APP_AUTHOR = "ChapterScore"


def _find_dotenv() -> Path | None:
    """Walk upward from cwd (and package root) looking for a .env file."""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",  # project root when editable
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Spotify
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # CLI OAuth local callback (spotipy opens a tiny server on this URI)
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    # Web OAuth redirect — Streamlit app origin (e.g. https://your-app.streamlit.app/)
    # If empty, the web UI auto-detects from request headers / localhost:8501
    spotify_web_redirect_uri: str = ""
    # Base scopes; auth.required_scope_string() always merges REQUIRED_SCOPES on top.
    spotify_scope: str = (
        "playlist-modify-public playlist-modify-private "
        "user-read-private user-read-email user-top-read"
    )

    # xAI / Grok
    xai_api_key: str = ""
    xai_model: str = "grok-4.5"
    xai_base_url: str = "https://api.x.ai/v1"

    # Optional book APIs
    google_books_api_key: str = ""

    # Tuning
    chapterscore_tracks_per_chapter: int = Field(default=3, ge=1, le=15)
    chapterscore_tracks_overall: int = Field(default=20, ge=5, le=100)
    # Desired candidates per query (paginated in pages of ≤10 — many apps cap limit at 10)
    # Quality-over-speed: larger candidate pools (paginated ≤10/page)
    chapterscore_max_search_results: int = Field(default=30, ge=5, le=50)
    # Soft floor: aim for at least this many tracks / hours when possible
    chapterscore_min_tracks: int = Field(default=12, ge=1, le=100)
    chapterscore_min_hours: float = Field(default=1.5, ge=0.0, le=6.0)
    # Spotify resilience — longer budget for thorough cinematic searches
    chapterscore_spotify_timeout: float = Field(
        default=12.0, ge=3.0, le=30.0, description="Hard wall-clock timeout per Spotify API call (s)"
    )
    chapterscore_spotify_requests_timeout: float = Field(
        default=10.0, ge=2.0, le=30.0, description="requests library timeout for spotipy (s)"
    )
    chapterscore_spotify_max_429_sleep: float = Field(
        default=3.0, ge=0.0, le=15.0, description="Max seconds to sleep on HTTP 429"
    )
    chapterscore_spotify_collection_budget: float = Field(
        default=180.0,
        ge=20.0,
        le=600.0,
        description="Global wall-clock budget for the entire track-collection phase (s)",
    )
    chapterscore_cache_dir: str = ""
    chapterscore_cache_ttl_hours: int = Field(default=168, ge=1)  # 7 days
    chapterscore_http_timeout: float = 30.0
    # Wikipedia and some APIs require a descriptive UA (not a generic bot string).
    chapterscore_user_agent: str = (
        "ChapterScore/0.1 (macOS CLI; book soundtrack playlists; "
        "https://github.com/chapterscore/chapterscore; contact: chapterscore@localhost)"
    )

    @field_validator("spotify_client_id", "spotify_client_secret", "xai_api_key", mode="before")
    @classmethod
    def strip_quotes(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().strip('"').strip("'")
        return v

    # ── Derived paths ────────────────────────────────────────────────────────

    @property
    def cache_dir(self) -> Path:
        if self.chapterscore_cache_dir:
            p = Path(self.chapterscore_cache_dir).expanduser()
        else:
            p = Path(user_cache_dir(APP_NAME, APP_AUTHOR))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        p = Path(user_data_dir(APP_NAME, APP_AUTHOR))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def config_dir(self) -> Path:
        p = Path(user_config_dir(APP_NAME, APP_AUTHOR))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def spotify_token_path(self) -> Path:
        return self.data_dir / "spotify_token_cache.json"

    def missing_required(self, need_spotify: bool = True, need_xai: bool = True) -> list[str]:
        missing: list[str] = []
        if need_spotify:
            if not self.spotify_client_id:
                missing.append("SPOTIFY_CLIENT_ID")
            if not self.spotify_client_secret:
                missing.append("SPOTIFY_CLIENT_SECRET")
        if need_xai and not self.xai_api_key:
            missing.append("XAI_API_KEY")
        return missing


@lru_cache
def get_settings() -> Settings:
    """Load settings once; reload by clearing the cache if .env changes."""
    dotenv = _find_dotenv()
    if dotenv is not None:
        # Ensure pydantic-settings picks up the discovered path
        os.environ.setdefault("DOTENV_PATH", str(dotenv))
        return Settings(_env_file=str(dotenv))  # type: ignore[call-arg]
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
