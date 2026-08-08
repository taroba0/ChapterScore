"""
ChapterScore — Streamlit web UI with in-browser Spotify OAuth.

Launch locally:
    streamlit run web/app.py

Deployed (Streamlit Community Cloud): set secrets + Redirect URIs (see README).
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
from chapterscore.models import LyricsPreference, Mode, PersonalizationPrefs, TasteStrength
from chapterscore.pipeline import generate_playlist
from chapterscore.spotify.web_auth import (
    SS_USER,
    clear_session_auth,
    get_session_spotify,
    load_streamlit_secrets_into_env,
    login_button_meta,
    process_oauth_callback,
    resolve_web_redirect_uri,
    session_has_playlist_permission,
    session_is_authenticated,
    session_token_scopes,
)


# ── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ChapterScore",
    page_icon="📚",
    layout="centered",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {
        padding-top: 2.25rem;
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
        margin-bottom: 1.5rem;
      }
      .stButton > button, .stLinkButton > a {
        width: 100%;
        font-weight: 600;
        padding: 0.65rem 1rem;
        border-radius: 0.6rem;
      }
      div[data-testid="stMetricValue"] { font-size: 1.35rem; }
      .result-card {
        background: linear-gradient(145deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 1rem;
        padding: 1.25rem 1.5rem;
        margin-top: 0.5rem;
      }
      footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _map_mode(label: str) -> Mode:
    return Mode.CHAPTER if label == "chapter" else Mode.OVERALL


def _map_lyrics(label: str) -> LyricsPreference:
    return {
        "Allow lyrics": LyricsPreference.ALLOW_LYRICS,
        "Prefer instrumental": LyricsPreference.PREFER_INSTRUMENTAL,
        "Instrumental only": LyricsPreference.INSTRUMENTAL_ONLY,
        # legacy labels if any linger in session
        "instrumental-only": LyricsPreference.INSTRUMENTAL_ONLY,
        "no": LyricsPreference.ALLOW_LYRICS,
        "yes": LyricsPreference.ALLOW_LYRICS,
    }.get(label, LyricsPreference.ALLOW_LYRICS).normalized()


def _friendly_error(exc: BaseException) -> str:
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
            "1. Spotify Developer Dashboard → your app → **User Management**: "
            "add this Spotify account to the allowlist (Development mode)\n"
            "2. Log out and **Login with Spotify** again; accept all permissions\n"
            "3. Confirm Redirect URI matches exactly (see sidebar)\n"
            "4. Playlists are created **private** by default"
        )
    if "xai" in low or "api key" in low:
        return f"{text}\n\nSet `XAI_API_KEY` in Streamlit Secrets or `.env`."
    if "redirect" in low or "invalid_grant" in low:
        return (
            f"{text}\n\n"
            "Redirect URI mismatch is the usual cause. Add the exact URI from the "
            "sidebar to your Spotify app settings."
        )
    return text


def _handle_oauth_redirect() -> None:
    """Process ?code= / ?error= from Spotify and clean the URL."""
    try:
        qp = dict(st.query_params)
    except Exception:
        qp = {}

    handled, err = process_oauth_callback(query_params=qp, session_state=st.session_state)
    if not handled:
        return

    # Clear OAuth params from the address bar
    try:
        st.query_params.clear()
    except Exception:
        for k in ("code", "state", "error", "error_description"):
            try:
                del st.query_params[k]
            except Exception:
                pass

    if err:
        st.session_state["auth_flash_error"] = err
    else:
        st.session_state["auth_flash_ok"] = True


def _render_sidebar(settings) -> None:
    with st.sidebar:
        st.markdown("### Spotify")
        redirect = resolve_web_redirect_uri()
        st.caption("Redirect URI (add this in Spotify Dashboard):")
        st.code(redirect, language=None)

        if session_is_authenticated(st.session_state):
            user = st.session_state.get(SS_USER) or {}
            name = user.get("display_name") or user.get("id") or "Spotify user"
            st.success(f"Logged in as **{name}**")
            if user.get("id"):
                st.caption(f"ID: `{user.get('id')}`")
            scopes = session_token_scopes(st.session_state)
            if scopes:
                st.caption("Scopes: " + ", ".join(scopes))
            if session_has_playlist_permission(st.session_state):
                st.caption("Playlist permission: yes")
            else:
                st.warning("Missing playlist scopes — log in again and accept all.")

            if st.button("Log out of Spotify", use_container_width=True):
                clear_session_auth(st.session_state)
                st.rerun()
        else:
            st.caption("Connect Spotify to create real playlists.")
            meta = login_button_meta(st.session_state)
            st.link_button(
                "Login with Spotify",
                meta["authorize_url"],
                type="primary",
                use_container_width=True,
            )
            with st.expander("OAuth details"):
                st.write("Scopes requested:")
                st.code(meta["scopes"])
                st.write(
                    "After you approve, Spotify returns here with a `code` "
                    "that we exchange server-side for tokens (stored in this session only)."
                )

        st.divider()
        st.markdown("### Secrets")
        st.caption(
            "Cloud: **App settings → Secrets**  \n"
            "Local: `.env` in the project root"
        )
        need = settings.missing_required(need_spotify=True, need_xai=True)
        if need:
            st.warning("Missing: " + ", ".join(need))
        else:
            st.caption("Credentials loaded.")

        st.divider()
        st.caption(f"ChapterScore v{__version__}")
        st.caption("CLI auth still works separately.")


def main() -> None:
    # Secrets → env before Settings loads
    load_streamlit_secrets_into_env()
    reload_settings()
    settings = get_settings()

    _handle_oauth_redirect()

    # Flash messages from OAuth
    if st.session_state.pop("auth_flash_ok", None):
        st.toast("Spotify connected", icon="✅")
    flash_err = st.session_state.pop("auth_flash_error", None)
    if flash_err:
        st.error(flash_err)

    st.markdown("# ChapterScore")
    st.markdown(
        '<p class="subtitle">Soundtracks for the books you love</p>',
        unsafe_allow_html=True,
    )

    _render_sidebar(settings)

    logged_in = session_is_authenticated(st.session_state)

    # Main-page login CTA when not authenticated
    if not logged_in:
        st.info(
            "Connect Spotify to create playlists. Dry-run analysis works without login."
        )
        meta = login_button_meta(st.session_state)
        c1, c2 = st.columns([1, 1])
        with c1:
            st.link_button(
                "Login with Spotify",
                meta["authorize_url"],
                type="primary",
                use_container_width=True,
            )
        with c2:
            st.caption(f"Redirect: `{meta['redirect_uri']}`")

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
                "Vocals / instrumental",
                options=["Allow lyrics", "Prefer instrumental", "Instrumental only"],
                index=0,
                help=(
                    "Allow lyrics = vocals OK · "
                    "Prefer instrumental = soft bias · "
                    "Instrumental only = hard filter (no clear vocals)"
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
                help="Target playlist length. Set 0 to disable.",
            )
        with col4:
            st.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
            dry_run = st.checkbox(
                "Dry run (analyze only — no Spotify playlist)",
                value=False,
            )

        st.markdown("##### Personalization")
        st.caption(
            "Priority: **(1)** vocals policy → **(2)** book style → "
            "**(3)** exploration → **(4)** Top Artists (soft)."
        )
        instrumental_only = lyrics_label == "Instrumental only"
        if instrumental_only:
            st.info(
                "Top Artists is disabled in **Instrumental only** mode because "
                "most top artists contain vocals."
            )
            taste_label = "disable"
        else:
            taste_label = st.selectbox(
                "Personal taste (Top Artists)",
                options=["disable", "top5", "top10", "top15"],
                index=2,
                format_func=lambda x: {
                    "disable": "Disable — book vibe only",
                    "top5": "Top 5 artists",
                    "top10": "Top 10 artists (recommended)",
                    "top15": "Top 15 artists",
                }[x],
                help="How many of your Spotify Top Artists to use as soft seeds.",
                disabled=False,
            )
        use_recs = st.toggle(
            "Use Spotify Recommendations API",
            value=True,
            help=(
                "When on, seed Recommendations with book vibe "
                "(+ your artists when Top Artists is enabled). "
                "Falls back to search if the API is unavailable."
            ),
        )
        exploration = st.slider(
            "Exploration vs comfort",
            min_value=0,
            max_value=100,
            value=40,
            help=(
                "0 = stick close to artists you already love · "
                "100 = discover more new artists (still matching the book). "
                "Never overrides instrumental or book-style rules."
            ),
        )
        st.caption(
            f"{'← Comfort' if exploration < 50 else 'Explore →'}  "
            f"Current: **{exploration}** "
            f"({'mostly familiar' if exploration <= 35 else 'balanced' if exploration <= 65 else 'mostly new'})"
        )

        submitted = st.form_submit_button("Generate Playlist", type="primary")

    if not submitted:
        return

    if not (title or "").strip():
        st.error("Please enter a book title.")
        return

    need_spotify = not dry_run
    missing_now = settings.missing_required(need_spotify=need_spotify, need_xai=True)
    if missing_now:
        st.error(
            "Missing configuration: "
            + ", ".join(missing_now)
            + ". Add them to Streamlit Secrets or `.env`."
        )
        return

    if need_spotify and not session_is_authenticated(st.session_state):
        st.error(
            "Please **Login with Spotify** first (sidebar or button above) "
            "to create a real playlist. Or enable **Dry run** for analysis only."
        )
        return

    status_box = st.status("Starting…", expanded=True)

    def progress(msg: str) -> None:
        status_box.update(label=msg[:80], state="running")
        status_box.write(f"▸ {msg}")

    sp = None
    if need_spotify:
        try:
            sp = get_session_spotify(st.session_state)
        except ChapterScoreError as exc:
            status_box.update(label="Auth failed", state="error")
            st.error(_friendly_error(exc))
            return

    taste_map = {
        "disable": TasteStrength.DISABLE,
        "top5": TasteStrength.TOP_5,
        "top10": TasteStrength.TOP_10,
        "top15": TasteStrength.TOP_15,
    }
    prefs = PersonalizationPrefs(
        taste_strength=taste_map[taste_label],
        use_recommendations=bool(use_recs),
        exploration=int(exploration),
    )

    try:
        # spotify_client: session OAuth client from browser login (None for dry-run)
        gen_kwargs = dict(
            author=author.strip() or None,
            mode=_map_mode(mode_label),
            lyrics=_map_lyrics(lyrics_label),
            min_hours=float(min_hours) if min_hours and min_hours > 0 else 0.0,
            public=False,
            dry_run=dry_run,
            use_cache=True,
            progress=progress,
            spotify_client=sp,
            personalization=prefs,
        )
        result = generate_playlist(title.strip(), **gen_kwargs)
        status_box.update(label="Done", state="complete")
    except TypeError as exc:
        # Helps diagnose stale deploys that still run an old pipeline.py
        if "spotify_client" in str(exc):
            status_box.update(label="Deploy outdated", state="error")
            st.error(
                "This deployment is running an **old** `generate_playlist` without "
                "`spotify_client` support.\n\n"
                f"Loaded pipeline: `{generate_playlist.__code__.co_filename}`\n"
                f"ChapterScore version: **{__version__}** (need ≥ 0.1.1)\n\n"
                "Push the latest `src/chapterscore/pipeline.py` to GitHub and "
                "**reboot** the Streamlit Cloud app."
            )
            return
        status_box.update(label="Failed", state="error")
        st.error(_friendly_error(exc))
        return
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

    # ── Results ──────────────────────────────────────────────────────────────
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
        st.write(
            f"**{analysis.playlist_title_suggestion or f'ChapterScore: {book.title}'}**"
        )
        if analysis.playlist_description:
            st.write(analysis.playlist_description)
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
