"""
ChapterScore — Streamlit web UI

Launch from the project root (with the package installed / venv active):

    streamlit run web/app.py

Uses the same pipeline as the CLI (`chapterscore.pipeline.generate_playlist`).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `streamlit run web/app.py` without an editable install
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st

from chapterscore import __version__
from chapterscore.config import get_settings, reload_settings
from chapterscore.exceptions import ChapterScoreError
from chapterscore.models import LyricsPreference, Mode
from chapterscore.pipeline import generate_playlist
from chapterscore.spotify.auth import auth_status, diagnose_spotify


# ── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ChapterScore",
    page_icon="📚",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      /* Clean, airy layout */
      .block-container {
        padding-top: 2.5rem;
        padding-bottom: 3rem;
        max-width: 720px;
      }
      h1 {
        font-weight: 700 !important;
        letter-spacing: -0.02em;
        margin-bottom: 0.25rem !important;
      }
      .subtitle {
        color: #6b7280;
        font-size: 1.05rem;
        margin-bottom: 1.75rem;
      }
      .stButton > button {
        width: 100%;
        font-weight: 600;
        padding: 0.65rem 1rem;
        border-radius: 0.6rem;
      }
      div[data-testid="stMetricValue"] {
        font-size: 1.35rem;
      }
      .result-card {
        background: linear-gradient(145deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 1rem;
        padding: 1.25rem 1.5rem;
        margin-top: 0.5rem;
      }
      .spotify-link a {
        font-weight: 600;
        font-size: 1.05rem;
      }
      /* Soften Streamlit chrome a bit */
      footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _map_mode(label: str) -> Mode:
    return Mode.CHAPTER if label == "chapter" else Mode.OVERALL


def _map_lyrics(label: str) -> LyricsPreference:
    return {
        "instrumental-only": LyricsPreference.INSTRUMENTAL_ONLY,
        "no": LyricsPreference.NO,
        "yes": LyricsPreference.YES,
    }[label]


def _friendly_error(exc: BaseException) -> str:
    """Turn exceptions into readable UI messages."""
    if isinstance(exc, ChapterScoreError):
        msg = exc.message
        if exc.hint:
            msg = f"{msg}\n\n{exc.hint}"
        return msg
    text = str(exc)
    low = text.lower()
    if "403" in text or "forbidden" in low:
        return (
            "Spotify returned **403 Forbidden** while creating the playlist.\n\n"
            "**Fix checklist**\n"
            "1. Add your account to the Spotify app **allowlist** (Developer Dashboard → User Management)\n"
            "2. Re-auth with full scopes:\n"
            "   `chapterscore logout && chapterscore auth --force`\n"
            "3. Development mode only works for allowlisted users\n"
            "4. Prefer a **private** playlist (this app defaults to private)\n"
            "5. Run `chapterscore doctor` or use the sidebar diagnostic"
        )
    if "xai" in low or "api key" in low:
        return (
            f"{text}\n\n"
            "Set `XAI_API_KEY` in your `.env` file (https://console.x.ai)."
        )
    if "spotify" in low and ("auth" in low or "token" in low or "credential" in low):
        return (
            f"{text}\n\n"
            "Run in a terminal:\n"
            "```\nchapterscore auth --force\n```"
        )
    return text


def _render_auth_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Setup")
        st.caption("Spotify must be authorized once via the CLI.")
        st.code("chapterscore auth --force", language="bash")

        if st.button("Check Spotify status", use_container_width=True):
            with st.spinner("Checking…"):
                try:
                    diag = diagnose_spotify()
                    st.session_state["spotify_diag"] = diag
                except Exception as exc:
                    st.session_state["spotify_diag"] = {"error": str(exc), "api_ok": False}

        diag = st.session_state.get("spotify_diag")
        if diag:
            if diag.get("api_ok"):
                st.success(
                    f"Signed in as **{diag.get('display_name') or diag.get('user_id')}**"
                )
                st.caption(f"User ID: `{diag.get('user_id')}`")
                scopes = diag.get("granted_scopes") or []
                st.caption("Scopes: " + (", ".join(scopes) if scopes else "—"))
                if diag.get("has_playlist_permission"):
                    st.caption("Playlist permission: yes")
                else:
                    st.warning("Missing playlist-modify scopes. Run auth --force.")
            else:
                st.warning(diag.get("error") or "Not authenticated")

        st.divider()
        st.caption(f"ChapterScore v{__version__}")
        st.caption("CLI remains fully supported.")


def main() -> None:
    reload_settings()
    settings = get_settings()

    # Header
    st.markdown("# ChapterScore")
    st.markdown(
        '<p class="subtitle">Soundtracks for the books you love</p>',
        unsafe_allow_html=True,
    )

    _render_auth_sidebar()

    # Config warnings (non-blocking)
    missing = settings.missing_required(need_spotify=True, need_xai=True)
    if missing:
        st.info(
            "Missing configuration: **"
            + ", ".join(missing)
            + "**. Add them to `.env` in the project root. "
            "Dry-run only needs `XAI_API_KEY`."
        )

    st_auth = auth_status()
    if not st_auth.get("token_cached") and not missing:
        st.warning(
            "Spotify is not authorized yet. Run "
            "`chapterscore auth --force` in a terminal first "
            "(unless you only need dry-run analysis)."
        )

    # ── Form ────────────────────────────────────────────────────────────────
    with st.form("generate_form", clear_on_submit=False):
        st.markdown("##### Book")
        title = st.text_input(
            "Book title",
            placeholder="e.g. Dune",
            help="Required. Use the full title for best metadata matches.",
        )
        author = st.text_input(
            "Author (optional)",
            placeholder="e.g. Frank Herbert",
        )

        st.markdown("##### Playlist options")
        col1, col2 = st.columns(2)
        with col1:
            mode_label = st.selectbox(
                "Mode",
                options=["overall", "chapter"],
                index=0,
                help="Overall = one cohesive mix. Chapter = ordered by narrative arcs.",
            )
        with col2:
            lyrics_label = st.selectbox(
                "Lyrics preference",
                options=["instrumental-only", "no", "yes"],
                index=0,
                help=(
                    "instrumental-only = soundtrack / no vocals · "
                    "no = either · yes = prefer songs with lyrics"
                ),
            )

        col3, col4 = st.columns(2)
        with col3:
            min_hours = st.number_input(
                "Minimum hours",
                min_value=0.0,
                max_value=6.0,
                value=1.5,
                step=0.5,
                help="Target playlist length. Set 0 to disable the duration target.",
            )
        with col4:
            st.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
            dry_run = st.checkbox(
                "Dry run (analyze only — no Spotify playlist)",
                value=False,
                help="Fetches book data and vibe analysis without searching or writing to Spotify.",
            )

        submitted = st.form_submit_button("Generate Playlist", type="primary")

    # ── Run ─────────────────────────────────────────────────────────────────
    if submitted:
        if not (title or "").strip():
            st.error("Please enter a book title.")
            return

        need_spotify = not dry_run
        need_xai = True
        missing_now = settings.missing_required(
            need_spotify=need_spotify, need_xai=need_xai
        )
        if missing_now:
            st.error(
                "Missing configuration: "
                + ", ".join(missing_now)
                + ". Copy `.env.example` → `.env` and fill in credentials."
            )
            return

        status_box = st.status("Starting…", expanded=True)
        log_lines: list[str] = []

        def progress(msg: str) -> None:
            log_lines.append(msg)
            # Keep status readable — show last few lines
            status_box.update(label=msg[:80], state="running")
            status_box.write(f"▸ {msg}")

        try:
            result = generate_playlist(
                title.strip(),
                author=author.strip() or None,
                mode=_map_mode(mode_label),
                lyrics=_map_lyrics(lyrics_label),
                min_hours=float(min_hours) if min_hours and min_hours > 0 else 0.0,
                public=False,  # private — safer in Spotify Development mode
                dry_run=dry_run,
                use_cache=True,
                progress=progress,
            )
            status_box.update(label="Done", state="complete")
        except ChapterScoreError as exc:
            status_box.update(label="Failed", state="error")
            st.error(_friendly_error(exc))
            return
        except Exception as exc:
            status_box.update(label="Failed", state="error")
            st.error(_friendly_error(exc))
            with st.expander("Technical details"):
                st.exception(exc)
            return

        # ── Results ──────────────────────────────────────────────────────────
        st.success("Generation complete" + (" (dry run)" if dry_run else ""))

        book = result.book
        analysis = result.analysis

        st.markdown("##### Book & vibe")
        m1, m2, m3 = st.columns(3)
        m1.metric("Mood", analysis.overall_mood or "—")
        m2.metric("Energy", f"{analysis.overall_energy:.2f}")
        m3.metric("Mode", mode_label)

        vibe_bits = []
        if analysis.atmospheres:
            vibe_bits.append(", ".join(analysis.atmospheres[:6]))
        if analysis.emotional_arc:
            vibe_bits.append(analysis.emotional_arc)
        if analysis.playlist_description:
            vibe_bits.append(analysis.playlist_description)
        if vibe_bits:
            st.info(" · ".join(vibe_bits[:3]))

        st.caption(
            f"**{book.display_name}**"
            + (f" · {book.publish_year}" if book.publish_year else "")
            + f" · sources: {book.source or '—'}"
        )

        if dry_run:
            st.markdown("##### Would create")
            st.write(f"**{analysis.playlist_title_suggestion or f'ChapterScore: {book.title}'}**")
            if analysis.playlist_description:
                st.write(analysis.playlist_description)
            if analysis.overall_search_queries:
                with st.expander("Sample Spotify search queries"):
                    for q in analysis.overall_search_queries[:8]:
                        st.write(f"• `{q.query}`")
            st.caption("Uncheck **Dry run** and generate again to create a real playlist.")
            return

        if result.playlist:
            pl = result.playlist
            st.markdown("##### Your playlist")
            st.markdown(
                f"""
                <div class="result-card">
                  <div style="font-size:1.25rem;font-weight:700;margin-bottom:0.35rem;">
                    {pl.name}
                  </div>
                  <div style="color:#64748b;margin-bottom:0.75rem;">
                    {pl.track_count} tracks
                    {" · " + (analysis.playlist_description or "") if analysis.playlist_description else ""}
                  </div>
                  <div class="spotify-link">
                    <a href="{pl.url}" target="_blank" rel="noopener">Open in Spotify ↗</a>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.link_button("Open Spotify playlist", pl.url, use_container_width=True)

        if result.tracks:
            with st.expander(f"Track list ({len(result.tracks)})", expanded=False):
                for i, t in enumerate(result.tracks, 1):
                    ch = f" · ch {t.chapter_number}" if t.chapter_number is not None else ""
                    st.write(f"{i}. **{t.name}** — {t.artist_str}{ch}")


if __name__ == "__main__":
    main()
