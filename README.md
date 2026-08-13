# ChapterScore

**Soundtracks for the books you love.**

ChapterScore builds Spotify playlists matching the emotional vibe, atmosphere, and storyline of a book — optionally chapter by chapter. Use the **CLI** or a simple **Streamlit web UI**.

It combines:

| Layer | Source |
|--------|--------|
| Book metadata, plot, reception | Open Library, Google Books, Wikipedia (+ themes/reception) |
| Literary vibe analysis | [xAI Grok](https://docs.x.ai) — literature-first dual pass (`litv2`) |
| Music search & playlists | Spotify Web API via [spotipy](https://spotipy.readthedocs.io/) |
| CLI UX | [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) |
| Web UI | [Streamlit](https://streamlit.io/) (`web/app.py`) |

---

## Features

- **Overall or chapter mode** — one cohesive playlist, or tracks ordered by chapter / emotional act
- **Literature-first multi-dimensional vibe** — voice, tone, intimacy scale, setting, distinctive signature (anti-generic)
- **Lyrics control** — prefer vocals, allow either, or force instrumental / soundtrack-only
- **Real book data first** — public metadata, plot, reception, and themes before LLM analysis
- **Smart track ranking** — book-vibe fit first, then style clash, popularity, diversity
- **Secure Spotify OAuth** — browser login, token cache with auto-refresh
- **Disk cache** — book lookups and vibe analyses cached locally (7-day TTL)
- **Polished CLI** — progress output, rich literary profile table, `doctor`, `--dry-run`
- **Streamlit web UI** — guided **2-step** flow (confirm book → personalize → generate)

---

## Requirements

- **macOS** (Linux/Windows may work; OAuth redirect is localhost)
- **Python 3.11+**
- Spotify account + [Developer app](https://developer.spotify.com/dashboard)
- [xAI API key](https://console.x.ai)

---

## Quick start

### 1. Install

```bash
cd ChapterScore
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```env
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
XAI_API_KEY=...
```

#### Spotify app setup

1. Open [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. **Create app** → name it e.g. `ChapterScore`
3. **Settings** → add **Redirect URI**: `http://127.0.0.1:8888/callback`
4. Copy **Client ID** and **Client Secret** into `.env`

#### xAI (Grok) setup

1. Sign up at [accounts.x.ai](https://accounts.x.ai) and add credits
2. Create a key at [console.x.ai](https://console.x.ai)
3. Put it in `.env` as `XAI_API_KEY`

### 3. Verify & authorize

```bash
chapterscore doctor          # check config + network
chapterscore auth --force    # browser OAuth (accept all playlist scopes)
```

### 4a. Web UI (browser Spotify login) — 2-step flow

```bash
cd ChapterScore
source .venv/bin/activate
streamlit run web/app.py
```

Open the URL Streamlit prints (usually **http://localhost:8501**).

1. Click **Login with Spotify** (sidebar) if you want a real playlist.
2. **Step 1** — enter title/author → **Analyze Book** → confirm the match.
3. **Step 2** unlocks only after confirmation — mode, length, lyrics, taste → **Generate Playlist**.

| Step | What you do |
|------|-------------|
| **1 · Book** | Title (required), author (optional) → **Search books** (multi-strategy Open Library + Google Books, ranked candidates) → pick match, pages, reading-time estimate → **Continue** |
| **2 · Personalize** | Mode (Chapter disabled if no chapter-list hint) · soft playlist length · lyrics · Top Artists / comfort / recommendations → **Generate** (full Grok vibe + Spotify) |

Step 2 stays locked/greyed out until Step 1 is confirmed. CLI remains single-step.

#### Spotify Redirect URIs (required)

In [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) → your app → **Settings** → **Redirect URIs**, add **all** that you use:

| Environment | Redirect URI (exact) |
|-------------|----------------------|
| CLI | `http://127.0.0.1:8888/callback` |
| Web local | `http://localhost:8501/` |
| Web local (alt) | `http://127.0.0.1:8501/` |
| Streamlit Cloud | `https://YOUR-APP-NAME.streamlit.app/` |

The sidebar shows the exact redirect URI the app will use. **Trailing slash matters** — copy it exactly.

#### Streamlit Community Cloud secrets

**App settings → Secrets** (TOML):

```toml
SPOTIFY_CLIENT_ID = "..."
SPOTIFY_CLIENT_SECRET = "..."
XAI_API_KEY = "..."

# Recommended: pin the cloud redirect so it never drifts
SPOTIFY_WEB_REDIRECT_URI = "https://YOUR-APP-NAME.streamlit.app/"
```

Also add your Spotify account under the app’s **User Management** allowlist (Development mode).

#### Test login on the deployed site

1. Open `https://YOUR-APP-NAME.streamlit.app/`
2. Confirm the sidebar **Redirect URI** matches a URI in the Spotify Dashboard
3. Click **Login with Spotify** → approve → land back on the app with “Logged in as …”
4. Generate a playlist (dry-run off) → open the Spotify link

Web tokens live in `st.session_state` for the browser session only (refresh token is used to renew access). CLI file-token auth is unchanged.

### 4b. CLI

```bash
# Cohesive playlist for a whole book
chapterscore generate "The Great Gatsby"

# With author (better metadata match)
chapterscore generate "Dune" --author "Frank Herbert"

# Chapter-by-chapter instrumental soundtrack
chapterscore generate "Dune" -a "Frank Herbert" \
  --mode chapter \
  --lyrics instrumental-only

# Prefer songs with lyrics
chapterscore generate "Normal People" -a "Sally Rooney" --lyrics yes

# Analyze only (no Spotify writes)
chapterscore generate "1984" --dry-run

# Custom size / name
chapterscore generate "Circe" -a "Madeline Miller" --tracks 25 --name "Circe · Island Hymns"
```

Open the printed Spotify URL — the playlist is already in your library.

---

## CLI reference

```
chapterscore generate TITLE [OPTIONS]
chapterscore lookup TITLE [--author] [--isbn]
chapterscore auth [--force]
chapterscore logout
chapterscore doctor
chapterscore cache [--clear]
chapterscore --version
chapterscore --help
```

### `generate` options

| Option | Default | Description |
|--------|---------|-------------|
| `-a, --author` | — | Author name |
| `-i, --isbn` | — | ISBN-10/13 |
| `-m, --mode` | `overall` | `overall` \| `chapter` |
| `-l, --lyrics` | `allow-lyrics` | `allow-lyrics` \| `prefer-instrumental` \| `instrumental-only` |
| `-n, --tracks` | `20` | Track count (overall mode) |
| `--tracks-per-chapter` | `3` | Tracks per chapter |
| `--public` | off | Public playlist |
| `--name` | auto | Custom playlist name |
| `--dry-run` | off | Book + Grok only |
| `--no-cache` | off | Bypass caches |
| `-t, --taste` | `top10` | `disable` \| `top5` \| `top10` \| `top15` (Spotify Top Artists) |
| `--recommendations` / `--no-recommendations` | on | Spotify Recommendations API |
| `-e, --exploration` | `40` | 0=comfort … 100=explore |

### Personalization (CLI examples)

```bash
# Stick close to your Top 10 artists
chapterscore generate "Dune" -a "Frank Herbert" -t top10 -e 20

# More discovery, still book-matched
chapterscore generate "Dune" -a "Frank Herbert" -t top5 -e 75

# Pure book vibe (no personal seeds)
chapterscore generate "Dune" -t disable --no-recommendations
```

After changing scopes, re-auth once:

```bash
chapterscore logout && chapterscore auth --force
```

(Web: **Log out** then **Login with Spotify** again so `user-top-read` is granted.)

---

## How it works

```
┌──────────────────┐   ┌────────────────────┐   ┌─────────────┐   ┌──────────────┐
│ Public book data │ → │ Literature-first   │ → │ Spotify     │ → │ Playlist in  │
│ OL / GB / Wiki / │   │ multi-pass Grok    │   │ search +    │   │ your account │
│ reception/themes │   │ (literary → music) │   │ vibe rank   │   │              │
└──────────────────┘   └────────────────────┘   └─────────────┘   └──────────────┘
```

### Stage 1 — Book information (public sources only)

| Signal | Source |
|--------|--------|
| Metadata, subjects, tags | Open Library |
| Publisher blurbs | Google Books |
| Plot / synopsis, chapter lists | Wikipedia |
| Critical reception, themes, style | Wikipedia section extraction |
| Tone language | Snippets mined from reception + blurbs (no illegal full text) |

Missing sources degrade gracefully: analysis still runs on whatever public text is available.

### Stage 2 — Literature-first vibe analysis (v0.4+)

Instead of one generic “music supervisor” pass, ChapterScore runs **two Grok passes**:

1. **Literary pass** — voice, dominant/secondary tones, humor & irony, intimacy vs epic scale, realism vs dreaminess, setting texture, sensory atmosphere, pacing profile, **distinctive signature**, genre-peer contrast, anti-generic notes, and emotional acts (when chapter data is weak).
2. **Music pass** — maps that profile into `suitable_styles`, `avoid_styles`, and Spotify search queries. Musical language is derived from the literary reading, not the other way around.

**Anti-generic rule:** two dystopian novels (e.g. *1984* vs *Dune*) or two coming-of-age stories should produce clearly different signatures, styles, and query banks — not the same “dark epic cinematic” or “melancholic piano” template.

### Stage 3 — Search & rank

- Query expansion uses voice, setting, suitable styles, intimacy band, and act-level cues  
- Ranking prioritizes **book vibe fit** over generic cinematic prestige  
- Intimate books penalize epic trailer / battle scores; epic books may use them  
- **Hard content filter** rejects podcasts, interviews, commentary, audiobook clips, and high-speechiness audio (all modes)  
- Instrumental-only remains a hard track-level filter when selected  
- Length (hours / track count) is a **soft target** — quality over padding  
- Overall mode stays shuffle-friendly; chapter mode is progression-aware, not time-synced  

### Optional: review analysis first (CLI)

```bash
# CLI — analyze, confirm, then create playlist
chapterscore generate "Dune" -a "Frank Herbert" --review-first
```

Web UI uses a dedicated **2-step flow** (book confirmation → personalization) instead.

### Stage 4 — Playlist

Named playlist with description (chapter / act vibe notes in chapter mode).

Book metadata and vibe analyses are cached (book cache `v2`, analysis cache `litv2`). Use `--no-cache` after upgrades.

---

## Testing that two similar-genre books differ

Dry-run two books in the same broad genre and compare the **Signature**, **Scale**, **Music styles**, and **Avoid styles** rows:

```bash
# Two dystopias — should NOT look the same
chapterscore generate "1984" -a "George Orwell" --dry-run --no-cache
chapterscore generate "Dune" -a "Frank Herbert" --dry-run --no-cache

# Two intimate literary / coming-of-age
chapterscore generate "Normal People" -a "Sally Rooney" --dry-run --no-cache
chapterscore generate "The Catcher in the Rye" -a "J. D. Salinger" --dry-run --no-cache
```

What “meaningfully different” looks like:

| Check | *1984* (typical) | *Dune* (typical) |
|-------|------------------|------------------|
| Signature | claustrophobic, clinical, psychological | ecological messianism, desert mythic scale |
| `intimacy_vs_epic` | high (intimate/personal dread) | low (epic/sweeping) |
| Music styles | cold ambient, bleak piano, tense minimalism | hybrid orchestral, desert ambient, ritual |
| Avoid / anti-generic | NOT epic battle / trailer | NOT bedroom indie / bubblegum |
| Queries | surveillance, bleak, electronic dread | desert, empire, sandstorm, space opera |

Unit tests encode the same idea without live APIs:

```bash
pytest tests/test_literary_vibe.py -q
```

---

## Project layout

```
ChapterScore/
├── pyproject.toml
├── .env.example
├── README.md
├── web/
│   └── app.py              # Streamlit web UI
├── src/chapterscore/
│   ├── cli.py              # Typer entrypoint
│   ├── pipeline.py         # End-to-end orchestration (shared by CLI + web)
│   ├── config.py           # Settings from .env
│   ├── models.py           # Pydantic domain models
│   ├── cache.py            # JSON disk cache
│   ├── books/              # Open Library, Google Books, Wikipedia
│   ├── analysis/           # Grok prompts + client
│   └── spotify/            # OAuth, search, ranking, playlists
└── tests/
```

### Design notes

- **Providers are swappable** — book fetchers and the music backend sit behind clear modules so a web UI or Apple Music backend can plug in later.
- **Structured LLM output** — Grok returns JSON validated by Pydantic; lyrics constraints are re-enforced in code.
- **Strict instrumental mode** — filters on `instrumentalness`, speechiness, and title cues; query text is nudged toward score/ambient language.
- **Tokens** — Spotify tokens live in the platform user data dir (not the repo), via spotipy’s cache handler with refresh.

---

## Development

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
ruff check src tests
```

### Useful first-run demos

```bash
chapterscore lookup "Pride and Prejudice" -a "Jane Austen"
chapterscore generate "The Hobbit" -a "J.R.R. Tolkien" --dry-run
chapterscore generate "The Hobbit" -a "J.R.R. Tolkien" -l instrumental-only -n 15
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Missing SPOTIFY_CLIENT_*` | Copy `.env.example` → `.env` and fill values; run from project dir or export vars |
| OAuth redirect error | Redirect URI must be exactly `http://127.0.0.1:8888/callback` in the Spotify dashboard |
| `XAI_API_KEY is not set` | Add key from https://console.x.ai |
| Empty / few tracks | Try `--lyrics no`, overall mode, or `--no-cache`; some niches are sparse on Spotify |
| Stale analysis | `chapterscore cache --clear` or `--no-cache` |
| Rate limits | Built-in retries + early-stop search; wait ~60s if Spotify returns quota exceeded |
| Empty tracks (old bug) | Fixed: search `limit` must be ≤10 (paginated); audio-features 403 is tolerated |
| `--min-tracks` / `--min-hours` | Soft length aims (defaults: 12 tracks / 1.5 hours); quality over padding |

Run **`chapterscore doctor`** anytime for a full environment check.

---

## Privacy

- Secrets stay in `.env` (gitignored) and the OS user data directory  
- Book/plot text is sent to the xAI API for analysis  
- Spotify tokens never leave your machine except to Spotify’s API  

---

## License

MIT
