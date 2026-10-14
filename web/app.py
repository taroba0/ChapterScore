"""
ChapterScore — Streamlit web UI with a guided 2-step flow.

  Step 1 — Fast book identity lookup + confirm (no Grok, no deep wiki)
  Step 2 — Personalize & generate (locked until Step 1 is confirmed;
            full literary vibe analysis runs here via generate_playlist)

After a playlist is generated, Copy feedback prefills an editable template
from that result. Copying it does not send anything to the model.

Launch locally:
    streamlit run web/app.py

Deployed (Streamlit Community Cloud): set secrets + Redirect URIs (see README).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

# Allow `streamlit run web/app.py` without an editable install
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_WEB = Path(__file__).resolve().parent
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

import streamlit as st
from feedback import build_feedback_template

from chapterscore import __version__
from chapterscore.books.aggregator import search_book_candidates
from chapterscore.config import get_settings, reload_settings
from chapterscore.exceptions import ChapterScoreError
from chapterscore.models import (
    BookMetadata,
    LyricsPreference,
    Mode,
    PersonalizationPrefs,
    TasteStrength,
)
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

# ── Session keys for the 2-step flow ─────────────────────────────────────────

SS_STEP1_DONE = "cs_step1_confirmed"  # bool — unlocks Step 2
SS_BOOK = "cs_book_data"  # confirmed BookMetadata.model_dump()
SS_CANDIDATES = "cs_candidates"  # list[BookMetadata dumps] from multi-search
SS_CANDIDATE_IDX = "cs_candidate_idx"  # selected radio index
SS_LOOKUP_TITLE = "cs_lookup_title"
SS_LOOKUP_AUTHOR = "cs_lookup_author"
SS_READING_HOURS = "cs_reading_hours"  # float, user-editable estimate
SS_RESULT = "cs_last_result"  # last generation snapshot for display
SS_FEEDBACK = "cs_feedback_text"  # editable copy-feedback draft
SS_FEEDBACK_FOR = "cs_feedback_for"  # result id the draft was built from
# Dark mode parked: light B&W only (no toggle).


# ── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ChapterScore",
    page_icon="📚",
    layout="centered",
    initial_sidebar_state="expanded",
)


def _inject_theme_css() -> None:
    """Inject light B&W theme CSS via st.html (not st.markdown).

    Streamlit 1.6x sanitizes <style> out of st.markdown(unsafe_allow_html=True),
    which left raw CSS rules visible as page text. st.html preserves <style>.
    Dark mode is parked — single light variable set only.
    """
    # One well-formed <style> block. Do not put </style> inside comments/strings.
    # Use st.html so the tag is not stripped (unlike st.markdown).
    st.html(
        """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,400;6..72,600;6..72,700&display=swap" rel="stylesheet">
<style>
  /* ChapterScore B&W light theme */
  :root, html, body, [data-testid="stAppViewContainer"], .stApp {
    --cs-bg: #ffffff;
    --cs-fg: #111111;
    --cs-muted: #6b6b6b;
    --cs-caption: #444444;
    --cs-border: #e5e5e5;
    --cs-card: #ffffff;
    --cs-card-muted: #fafafa;
    --cs-heading: #000000;
    --cs-primary-bg: #000000;
    --cs-primary-fg: #ffffff;
    --cs-secondary-bg: #ffffff;
    --cs-secondary-fg: #000000;
    --cs-secondary-hover: #f5f5f5;
    --cs-sidebar: #fafafa;
    --cs-locked-bg: #f5f5f5;
    --cs-locked-fg: #555555;
    --cs-locked-border: #dddddd;
    --cs-disabled-bg: #222222;
    --cs-disabled-fg: #ffffff;
    --cs-confirm: #fafafa;
    --cs-result-muted: #6b6b6b;
  }
  html, body, [data-testid="stAppViewContainer"], .stApp {
    background: var(--cs-bg) !important;
    color: var(--cs-fg) !important;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
  }
  .block-container {
    padding-top: 2.5rem;
    padding-bottom: 4rem;
    max-width: 740px;
  }

  /* Headings — Newsreader, left-aligned */
  h1, h2, h3, h4, h5, h6,
  [data-testid="stMarkdownContainer"] h1,
  [data-testid="stMarkdownContainer"] h2,
  [data-testid="stMarkdownContainer"] h3 {
    font-family: "Newsreader", "Times New Roman", Georgia, serif !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em;
    color: var(--cs-heading) !important;
  }
  h1, [data-testid="stMarkdownContainer"] h1 {
    text-align: left !important;
    font-size: 2.35rem !important;
    margin-bottom: 0.35rem !important;
    font-weight: 700 !important;
  }
  .subtitle {
    font-family: "Inter", sans-serif !important;
    color: var(--cs-muted) !important;
    font-size: 1.05rem;
    margin: 0 0 1.25rem 0;
    text-align: left;
    font-weight: 400;
  }

  /* Pin readable text on grounds (beats Streamlit Dark menu wash) */
  [data-testid="stMarkdownContainer"] p,
  [data-testid="stMarkdownContainer"] span,
  [data-testid="stMarkdownContainer"] li,
  [data-testid="stText"],
  .stMarkdown {
    color: var(--cs-fg) !important;
  }
  [data-testid="stCaptionContainer"],
  [data-testid="stCaptionContainer"] *,
  [data-testid="stCaptionContainer"] p,
  [data-testid="stCaptionContainer"] span {
    color: var(--cs-caption) !important;
  }
  label,
  [data-testid="stWidgetLabel"],
  [data-testid="stWidgetLabel"] p,
  [data-testid="stWidgetLabel"] span,
  [data-testid="stWidgetLabel"] label {
    color: var(--cs-fg) !important;
  }
  /* Radio / checkbox / select option labels */
  [data-testid="stRadio"] label,
  [data-testid="stCheckbox"] label,
  [role="radiogroup"] label,
  .stRadio label,
  .stCheckbox label {
    color: var(--cs-fg) !important;
  }

  /* Primary = solid; secondary = outlined */
  .stButton > button[kind="primary"],
  .stButton > button[data-testid="baseButton-primary"],
  button[data-testid="stBaseButton-primary"],
  .stLinkButton > a[data-testid="stBaseLinkButton-primary"] {
    background-color: var(--cs-primary-bg) !important;
    color: var(--cs-primary-fg) !important;
    border: 1px solid var(--cs-primary-bg) !important;
    opacity: 1 !important;
  }
  .stButton > button[kind="primary"]:hover,
  .stButton > button[data-testid="baseButton-primary"]:hover,
  button[data-testid="stBaseButton-primary"]:hover,
  .stLinkButton > a[data-testid="stBaseLinkButton-primary"]:hover {
    filter: brightness(1.08);
    color: var(--cs-primary-fg) !important;
    opacity: 1 !important;
  }
  .stButton > button, .stLinkButton > a {
    width: 100%;
    font-family: "Inter", sans-serif !important;
    font-weight: 600;
    padding: 0.65rem 1rem;
    border-radius: 0.35rem;
    border: 1px solid var(--cs-heading) !important;
    background-color: var(--cs-secondary-bg) !important;
    color: var(--cs-secondary-fg) !important;
    opacity: 1 !important;
  }
  .stButton > button:hover, .stLinkButton > a:hover {
    background-color: var(--cs-secondary-hover) !important;
    border-color: var(--cs-heading) !important;
    color: var(--cs-secondary-fg) !important;
  }
  /* Re-assert primary after the general rule */
  .stButton > button[kind="primary"],
  .stButton > button[data-testid="baseButton-primary"],
  button[data-testid="stBaseButton-primary"],
  .stLinkButton > a[data-testid="stBaseLinkButton-primary"] {
    background-color: var(--cs-primary-bg) !important;
    color: var(--cs-primary-fg) !important;
    border: 1px solid var(--cs-primary-bg) !important;
  }
  /* Disabled — dark gray fill + white/light text (readable, not black-on-black) */
  .stButton > button:disabled,
  .stButton > button[disabled],
  .stButton > button[kind="primary"]:disabled,
  .stButton > button[kind="primary"][disabled],
  .stButton > button[data-testid="baseButton-primary"]:disabled,
  .stButton > button[data-testid="baseButton-primary"][disabled],
  button[data-testid="stBaseButton-primary"]:disabled,
  button[data-testid="stBaseButton-primary"][disabled] {
    background-color: var(--cs-disabled-bg) !important;
    color: var(--cs-disabled-fg) !important;
    border: 1px solid var(--cs-disabled-bg) !important;
    opacity: 0.85 !important;
    cursor: not-allowed !important;
  }

  /* Streamlit 1.61: button labels are stMarkdownContainer p/span.
     Global --cs-fg pin would force black text inside black primary buttons.
     Make all button descendants inherit the button's own color. */
  .stButton > button [data-testid="stMarkdownContainer"],
  .stButton > button [data-testid="stMarkdownContainer"] p,
  .stButton > button [data-testid="stMarkdownContainer"] span,
  .stButton > button [data-testid="stMarkdownContainer"] *,
  .stButton > button *,
  .stLinkButton > a [data-testid="stMarkdownContainer"],
  .stLinkButton > a [data-testid="stMarkdownContainer"] p,
  .stLinkButton > a [data-testid="stMarkdownContainer"] span,
  .stLinkButton > a [data-testid="stMarkdownContainer"] *,
  .stLinkButton > a * {
    color: inherit !important;
  }
  /* Primary / disabled: re-pin descendants to explicit fg (beats inherit races) */
  .stButton > button[kind="primary"] [data-testid="stMarkdownContainer"],
  .stButton > button[kind="primary"] [data-testid="stMarkdownContainer"] *,
  .stButton > button[data-testid="baseButton-primary"] [data-testid="stMarkdownContainer"],
  .stButton > button[data-testid="baseButton-primary"] [data-testid="stMarkdownContainer"] *,
  .stButton > button[data-testid="baseButton-primary"] *,
  button[data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"],
  button[data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"] *,
  button[data-testid="stBaseButton-primary"] *,
  .stLinkButton > a[data-testid="stBaseLinkButton-primary"] [data-testid="stMarkdownContainer"],
  .stLinkButton > a[data-testid="stBaseLinkButton-primary"] [data-testid="stMarkdownContainer"] *,
  .stLinkButton > a[data-testid="stBaseLinkButton-primary"] * {
    color: var(--cs-primary-fg) !important;
  }
  .stButton > button:disabled [data-testid="stMarkdownContainer"],
  .stButton > button:disabled [data-testid="stMarkdownContainer"] *,
  .stButton > button:disabled *,
  .stButton > button[disabled] [data-testid="stMarkdownContainer"],
  .stButton > button[disabled] [data-testid="stMarkdownContainer"] *,
  .stButton > button[disabled] *,
  button[data-testid="stBaseButton-primary"]:disabled [data-testid="stMarkdownContainer"],
  button[data-testid="stBaseButton-primary"]:disabled [data-testid="stMarkdownContainer"] *,
  button[data-testid="stBaseButton-primary"]:disabled *,
  button[data-testid="stBaseButton-primary"][disabled] [data-testid="stMarkdownContainer"],
  button[data-testid="stBaseButton-primary"][disabled] [data-testid="stMarkdownContainer"] *,
  button[data-testid="stBaseButton-primary"][disabled] * {
    color: var(--cs-disabled-fg) !important;
  }

  /* Copy-feedback custom button — black bg + white text (and children) */
  #cs-copy-feedback {
    background-color: var(--cs-primary-bg) !important;
    color: var(--cs-primary-fg) !important;
    border: 1px solid var(--cs-primary-bg) !important;
    border-radius: 0.35rem !important;
    font-family: "Inter", sans-serif !important;
    font-weight: 600 !important;
    opacity: 1 !important;
  }
  #cs-copy-feedback, #cs-copy-feedback * {
    color: var(--cs-primary-fg) !important;
  }
  #cs-copy-feedback:hover {
    filter: brightness(1.08);
  }
  #cs-copy-feedback-status {
    color: var(--cs-caption) !important;
  }

  div[data-testid="stMetricValue"] {
    font-size: 1.35rem;
    font-family: "Inter", sans-serif !important;
    color: var(--cs-fg) !important;
  }
  div[data-testid="stMetricLabel"] {
    color: var(--cs-caption) !important;
  }
  .result-card {
    background: var(--cs-card);
    border: 1px solid var(--cs-border);
    border-radius: 0.5rem;
    padding: 1.25rem 1.5rem;
    margin-top: 0.5rem;
    color: var(--cs-fg);
  }
  .step-card {
    border: 1px solid var(--cs-border);
    border-radius: 0.5rem;
    padding: 1.25rem 1.35rem 1.35rem;
    margin-bottom: 1.25rem;
    background: var(--cs-card);
    color: var(--cs-fg);
  }
  /* Locked: muted colors only — no opacity stacking (avoids washed labels) */
  .step-card.locked {
    background: var(--cs-locked-bg);
    border-color: var(--cs-locked-border);
    color: var(--cs-locked-fg);
    opacity: 1 !important;
  }
  .step-card.locked h3,
  .step-card.locked .step-badge {
    color: var(--cs-locked-fg) !important;
  }
  .step-badge {
    display: inline-block;
    font-family: "Inter", sans-serif !important;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--cs-muted);
    margin-bottom: 0.35rem;
  }
  .step-badge.active { color: var(--cs-heading); }
  .step-badge.done { color: var(--cs-caption); }
  .confirm-box {
    background: var(--cs-confirm);
    border: 1px solid var(--cs-border);
    border-radius: 0.5rem;
    padding: 0.9rem 1rem;
    margin: 0.75rem 0;
    color: var(--cs-fg);
  }
  .cs-defaults-summary {
    font-family: "Inter", sans-serif !important;
    font-size: 0.95rem;
    color: var(--cs-fg);
    background: var(--cs-card-muted);
    border: 1px solid var(--cs-border);
    border-radius: 0.4rem;
    padding: 0.75rem 1rem;
    margin: 0.5rem 0 1rem 0;
    line-height: 1.45;
  }
  .step-card.locked .cs-defaults-summary {
    color: var(--cs-locked-fg);
    background: var(--cs-locked-bg);
    border-color: var(--cs-locked-border);
  }
  [data-testid="stSidebar"] {
    background: var(--cs-sidebar) !important;
    border-right: 1px solid var(--cs-border);
    color: var(--cs-fg) !important;
  }
  [data-testid="stSidebar"] p,
  [data-testid="stSidebar"] span,
  [data-testid="stSidebar"] label,
  [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {
    color: var(--cs-fg) !important;
  }
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {
    color: var(--cs-caption) !important;
  }
  /* Expander header readable */
  [data-testid="stExpander"] summary,
  [data-testid="stExpander"] summary span,
  [data-testid="stExpander"] summary p {
    color: var(--cs-fg) !important;
  }
  /* Inputs / select readable */
  .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div,
  textarea {
    color: var(--cs-fg) !important;
  }
  hr, [data-testid="stDivider"] {
    border-color: var(--cs-border) !important;
  }
  footer { visibility: hidden; }
</style>
"""
    )



# ── Helpers ──────────────────────────────────────────────────────────────────


def _map_lyrics(label: str) -> LyricsPreference:
    return {
        "Allow lyrics": LyricsPreference.ALLOW_LYRICS,
        "Prefer instrumental": LyricsPreference.PREFER_INSTRUMENTAL,
        "Instrumental only": LyricsPreference.INSTRUMENTAL_ONLY,
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


def estimate_reading_hours(page_count: int | None) -> float:
    """Default reading-time estimate (~35 pages/hour). Editable by the user."""
    if not page_count or page_count <= 0:
        return 6.0  # unknown length — mid-length novel default
    return round(max(0.5, min(40.0, page_count / 35.0)), 1)


def recommend_playlist_hours(reading_hours: float) -> float:
    """
    Soft playlist-length recommendation from reading-time estimate.

    Roughly ~40% of reading time, clamped to a practical listening window.
    Not a hard quota — generation still prefers quality over padding.
    """
    try:
        rh = float(reading_hours)
    except (TypeError, ValueError):
        rh = 6.0
    return round(min(3.0, max(0.5, rh * 0.4)), 1)


def _reset_step1_state() -> None:
    for k in (
        SS_STEP1_DONE,
        SS_BOOK,
        SS_CANDIDATES,
        SS_CANDIDATE_IDX,
        SS_READING_HOURS,
        SS_RESULT,
        SS_FEEDBACK,
        SS_FEEDBACK_FOR,
        "step1_reading_hours",
        "step1_pick_radio",
        "cs_has_real_chapters",  # legacy key cleanup
    ):
        st.session_state.pop(k, None)


def _candidate_label(book: BookMetadata, index: int) -> str:
    year = f" ({book.publish_year})" if book.publish_year else ""
    pages = f" · {book.page_count} pp" if book.page_count else ""
    rh = estimate_reading_hours(book.page_count)
    read = f" · ~{rh:g} h read" if book.page_count else ""
    return f"{index + 1}. {book.title} — {book.author_str}{year}{pages}{read}"


def _handle_oauth_redirect() -> None:
    """Process ?code= / ?error= from Spotify and clean the URL."""
    try:
        qp = dict(st.query_params)
    except Exception:
        qp = {}

    handled, err = process_oauth_callback(query_params=qp, session_state=st.session_state)
    if not handled:
        return

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
        st.caption("CLI remains single-step.")


def _render_step1(settings) -> None:
    confirmed = bool(st.session_state.get(SS_STEP1_DONE))
    badge = "done" if confirmed else "active"
    st.markdown(
        f'<div class="step-card">'
        f'<div class="step-badge {badge}">Step 1 · Find & confirm your book</div>'
        f"<h3 style='margin:0 0 0.75rem 0;font-size:1.2rem;'>Search catalogues</h3>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Multi-strategy search on Open Library + Google Books (fast identity only). "
        "Full literary vibe analysis runs later when you generate the playlist."
    )

    # Widget state via keys only (avoid value= + key= conflicts)
    if "step1_title_input" not in st.session_state:
        st.session_state["step1_title_input"] = st.session_state.get(SS_LOOKUP_TITLE, "")
    if "step1_author_input" not in st.session_state:
        st.session_state["step1_author_input"] = st.session_state.get(SS_LOOKUP_AUTHOR, "")

    title = st.text_input(
        "Book title",
        placeholder="e.g. I Who Have Never Known Men",
        help="Required. Partial titles often work — we'll show top matches.",
        key="step1_title_input",
    )
    author = st.text_input(
        "Author (optional)",
        placeholder="e.g. Jacqueline Harpman",
        help="Adding the author improves ranking when the title is common.",
        key="step1_author_input",
    )

    c1, c2 = st.columns(2)
    with c1:
        lookup_clicked = st.button(
            "Search books",
            type="primary",
            use_container_width=True,
            key="btn_lookup_book",
        )
    with c2:
        if confirmed or st.session_state.get(SS_BOOK) or st.session_state.get(SS_CANDIDATES):
            if st.button(
                "Start over / change book",
                use_container_width=True,
                key="btn_reset_book",
            ):
                _reset_step1_state()
                st.session_state[SS_LOOKUP_TITLE] = title
                st.session_state[SS_LOOKUP_AUTHOR] = author
                st.rerun()

    if lookup_clicked:
        if not (title or "").strip():
            st.error("Please enter a book title.")
        else:
            st.session_state[SS_STEP1_DONE] = False
            st.session_state.pop(SS_RESULT, None)
            st.session_state.pop(SS_FEEDBACK, None)
            st.session_state.pop(SS_FEEDBACK_FOR, None)
            st.session_state.pop(SS_BOOK, None)
            st.session_state.pop(SS_CANDIDATES, None)
            st.session_state.pop("step1_pick_radio", None)
            status = st.status("Searching catalogues…", expanded=True)
            try:
                status.write("▸ Open Library (exact / fuzzy / free-text)…")
                status.write("▸ Google Books (intitle / quoted / free-text)…")
                candidates = search_book_candidates(
                    title.strip(),
                    author=author.strip() or None,
                    limit=8,
                    use_cache=True,
                )
                status.write(f"▸ Ranked {len(candidates)} candidate(s)")
                status.update(label="Pick the correct book", state="complete")

                st.session_state[SS_CANDIDATES] = [
                    c.model_dump(mode="json") for c in candidates
                ]
                st.session_state[SS_CANDIDATE_IDX] = 0
                st.session_state[SS_LOOKUP_TITLE] = title.strip()
                st.session_state[SS_LOOKUP_AUTHOR] = (author or "").strip()
                st.rerun()
            except ChapterScoreError as exc:
                status.update(label="No strong match", state="error")
                st.error(_friendly_error(exc))
                st.info(
                    "**Tips:** try a shorter title fragment, check spelling, "
                    "add the author name, or use an ISBN-13."
                )
            except Exception as exc:
                status.update(label="Lookup failed", state="error")
                st.error(_friendly_error(exc))
                with st.expander("Technical details"):
                    st.exception(exc)

    # ── Multi-candidate picker ───────────────────────────────────────────
    raw_cands = st.session_state.get(SS_CANDIDATES) or []
    if raw_cands and not confirmed:
        candidates = [BookMetadata.model_validate(c) for c in raw_cands]
        st.markdown("##### Select the correct match")
        st.caption(
            f"Showing top {len(candidates)} result(s) for "
            f"**{st.session_state.get(SS_LOOKUP_TITLE, '')}**"
            + (
                f" · author filter: {st.session_state.get(SS_LOOKUP_AUTHOR)}"
                if st.session_state.get(SS_LOOKUP_AUTHOR)
                else ""
            )
            + ". Best guess is first."
        )

        labels = [_candidate_label(c, i) for i, c in enumerate(candidates)]
        pick = st.radio(
            "Candidates",
            options=list(range(len(candidates))),
            format_func=lambda i: labels[i],
            index=int(st.session_state.get(SS_CANDIDATE_IDX) or 0),
            key="step1_pick_radio",
            label_visibility="collapsed",
        )
        st.session_state[SS_CANDIDATE_IDX] = int(pick)
        book = candidates[int(pick)]

        st.markdown(
            '<div class="confirm-box">',
            unsafe_allow_html=True,
        )
        st.markdown(f"**{book.title}**")
        st.caption(
            f"by {book.author_str}"
            + (f" · {book.publish_year}" if book.publish_year else "")
            + (f" · {book.page_count} pages" if book.page_count else "")
            + f" · sources: {book.source or '—'}"
        )
        blurb = (book.publisher_blurb or book.description or "").strip()
        if blurb:
            st.write(blurb[:360] + ("…" if len(blurb) > 360 else ""))

        m1, m2, m3 = st.columns(3)
        m1.metric("Year", str(book.publish_year) if book.publish_year else "—")
        m2.metric("Pages", str(book.page_count) if book.page_count else "Unknown")
        default_rh = estimate_reading_hours(book.page_count)
        m3.metric("Est. reading", f"~{default_rh:g} h")

        # Editable reading-time (keyed per selection so it refreshes when pick changes)
        read_key = f"step1_reading_hours_{int(pick)}"
        if read_key not in st.session_state:
            st.session_state[read_key] = float(default_rh)
        reading_hours = st.number_input(
            "Estimated reading time (hours)",
            min_value=0.5,
            max_value=48.0,
            step=0.5,
            help=(
                "Default from page count (~35 pages/hour). "
                "Edit freely — used only to recommend playlist length."
            ),
            key=read_key,
        )
        rec = recommend_playlist_hours(float(reading_hours))
        st.caption(
            f"Suggested playlist length: **~{rec:g} hours** "
            f"(soft target — adjustable in Step 2)."
        )
        st.markdown("</div>", unsafe_allow_html=True)

        if st.button(
            "This is correct — Continue to personalization",
            type="primary",
            use_container_width=True,
            key="btn_confirm_book",
        ):
            st.session_state[SS_BOOK] = book.model_dump(mode="json")
            st.session_state[SS_READING_HOURS] = float(reading_hours)
            st.session_state["step1_reading_hours"] = float(reading_hours)
            st.session_state[SS_STEP1_DONE] = True
            st.rerun()

    # ── Confirmed summary (after Continue) ───────────────────────────────
    if confirmed and st.session_state.get(SS_BOOK):
        book = BookMetadata.model_validate(st.session_state[SS_BOOK])
        rh = float(st.session_state.get(SS_READING_HOURS) or estimate_reading_hours(book.page_count))
        st.success(
            f"Confirmed: **{book.display_name}**"
            + (f" · {book.publish_year}" if book.publish_year else "")
            + (f" · {book.page_count} pages" if book.page_count else "")
            + f" · ~{rh:g} h reading estimate"
        )
        st.caption(
            "Step 2 is unlocked — generate builds one cohesive overall reading playlist."
        )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_step2(settings) -> None:
    unlocked = bool(st.session_state.get(SS_STEP1_DONE))
    locked_cls = "" if unlocked else "locked"
    badge = "active" if unlocked else ""

    st.markdown(
        f'<div class="step-card {locked_cls}">'
        f'<div class="step-badge {badge}">Step 2 · Personalization & generate</div>'
        f"<h3 style='margin:0 0 0.5rem 0;font-size:1.2rem;'>Shape your playlist</h3>",
        unsafe_allow_html=True,
    )

    # ── Locked shell: no Generate / Personalize until Step 1 confirmed ──
    if not unlocked:
        st.caption(
            "🔒 Complete Step 1 and confirm the book to unlock Personalize & Generate."
        )
        st.markdown(
            '<div class="cs-defaults-summary">'
            "<strong>Step 2 is locked</strong><br>"
            "Select and confirm a book in Step 1. Then you can personalize length, "
            "lyrics, and taste, and generate your playlist."
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # ── Unlocked: defaults summary → Generate → Personalize expander ──
    book = BookMetadata.model_validate(st.session_state[SS_BOOK])
    reading_h = float(st.session_state.get(SS_READING_HOURS) or 6.0)
    rec_hours = recommend_playlist_hours(reading_h)

    # Session values start as recommended defaults; Personalize widgets mutate them.
    if "step2_length_mode" not in st.session_state:
        st.session_state["step2_length_mode"] = "Recommended"
    if "step2_custom_hours" not in st.session_state:
        st.session_state["step2_custom_hours"] = float(rec_hours)
    if "step2_track_count" not in st.session_state:
        st.session_state["step2_track_count"] = 20
    if "step2_lyrics" not in st.session_state:
        st.session_state["step2_lyrics"] = "Allow lyrics"
    if "step2_taste" not in st.session_state:
        st.session_state["step2_taste"] = "top10"
    if "step2_exploration" not in st.session_state:
        st.session_state["step2_exploration"] = 25
    if "step2_recs" not in st.session_state:
        st.session_state["step2_recs"] = True
    if "step2_dry_run" not in st.session_state:
        st.session_state["step2_dry_run"] = False

    st.caption(
        f"Book: **{book.display_name}** · "
        f"reading estimate **{reading_h:g} h** · "
        f"recommended playlist **~{rec_hours:g} h**"
    )
    st.caption(
        "Builds one cohesive overall reading playlist (literary vibe analysis + Spotify). "
        "Chapter-by-chapter mode is not offered in the web UI."
    )

    # Display-only summary of what Generate will use (not editable here)
    length_mode = st.session_state.get("step2_length_mode", "Recommended")
    lyrics_label = st.session_state.get("step2_lyrics", "Allow lyrics")
    if length_mode == "Recommended":
        length_summary = f"~{rec_hours:g} hours (recommended)"
    elif length_mode == "Custom hours":
        ch = float(st.session_state.get("step2_custom_hours", rec_hours))
        length_summary = f"{ch:g} hours (custom)"
    elif length_mode == "Track count instead":
        tc = int(st.session_state.get("step2_track_count", 20))
        length_summary = f"{tc} tracks"
    else:
        length_summary = "no length target"

    lyrics_note = ""
    if lyrics_label == "Allow lyrics":
        lyrics_note = (
            " — vocals recommended for campus romance books (not forced)"
        )

    st.markdown(
        f'<div class="cs-defaults-summary">'
        f"<strong>Defaults</strong> (used when you Generate)<br>"
        f"· Playlist length: {length_summary}<br>"
        f"· Lyrics: {lyrics_label}{lyrics_note}"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Generate first — prominent; uses session values
    st.markdown("**Generate**")
    gen_clicked = st.button(
        "Generate Playlist",
        type="primary",
        use_container_width=True,
        key="btn_generate",
    )

    # Personalize: only here can they change hours/length, lyrics, taste, etc.
    with st.expander("Personalize", expanded=False):
        st.caption(
            "Change length, lyrics, taste, exploration, and dry-run. "
            "Collapsed = Generate uses the defaults shown above."
        )
        st.markdown("**Playlist length**")
        st.caption(
            f"Recommended **~{rec_hours:g} hours** from your reading-time estimate "
            f"({reading_h:g} h). Soft target — fills in-style; will not pad with mismatched cinema."
        )
        length_mode = st.radio(
            "Length preference",
            options=[
                "Recommended",
                "Custom hours",
                "Track count instead",
                "No length target",
            ],
            format_func=lambda x: (
                f"Recommended (~{rec_hours:g} h)" if x == "Recommended" else x
            ),
            key="step2_length_mode",
        )

        if length_mode == "Custom hours":
            st.number_input(
                "Target hours",
                min_value=0.5,
                max_value=6.0,
                step=0.25,
                help="Serious duration aim — fills in-style until close.",
                key="step2_custom_hours",
            )
        elif length_mode == "Track count instead":
            st.number_input(
                "Target tracks",
                min_value=8,
                max_value=80,
                step=1,
                key="step2_track_count",
            )

        st.selectbox(
            "Lyrics preference",
            options=["Allow lyrics", "Prefer instrumental", "Instrumental only"],
            help=(
                "Allow lyrics = vocals OK · Prefer instrumental = soft bias · "
                "Instrumental only = hard reject for any credible vocals/speech "
                "(Top Artists still allowed as a soft seed)"
            ),
            key="step2_lyrics",
        )
        st.caption(
            "For campus / New Adult romance–ish vibes, **Allow lyrics** / vocals is "
            "recommended (not required — Prefer instrumental or Instrumental only still work)."
        )

        st.caption(
            "Priority: **(1)** vocals / content safety → **(2)** book atmosphere → "
            "**(3)** quality floor → **(4)** exploration → **(5)** Top Artists (soft)."
        )
        taste_label = st.selectbox(
            "Personal taste (Top Artists)",
            options=["disable", "top5", "top10", "top15"],
            format_func=lambda x: {
                "disable": "Disable — book vibe only",
                "top5": "Top 5 artists",
                "top10": "Top 10 artists (recommended)",
                "top15": "Top 15 artists",
            }[x],
            help=(
                "Soft seeds from your Spotify listening — never overrides book vibe "
                "or lyrics rules."
            ),
            key="step2_taste",
        )
        lyrics_now = st.session_state.get("step2_lyrics", "Allow lyrics")
        if lyrics_now == "Instrumental only" and taste_label != "disable":
            st.warning(
                "Note: many of your top artists have vocals, so results may be limited "
                "in **Instrumental only** mode. Top Artists stays enabled."
            )

        exploration = st.slider(
            "Comfort ↔ Explorative",
            min_value=0,
            max_value=100,
            help=(
                "0 = stick close to artists you already love · "
                "100 = discover more new artists (still matching the book)."
            ),
            key="step2_exploration",
        )
        st.caption(
            f"{'← Comfort' if exploration < 50 else 'Explore →'}  "
            f"**{exploration}** "
            f"({'mostly familiar' if exploration <= 35 else 'balanced' if exploration <= 65 else 'mostly new'})"
        )

        st.toggle(
            "Use Spotify Recommendations API",
            help=(
                "Seed Recommendations with book vibe (+ your artists when Top Artists is on). "
                "Falls back to search if unavailable."
            ),
            key="step2_recs",
        )

        st.checkbox(
            "Dry run (analyze / search plan only — no Spotify playlist write)",
            key="step2_dry_run",
        )

    st.markdown("</div>", unsafe_allow_html=True)

    if not gen_clicked:
        return

    # Bind Generate to session values (defaults unless Personalize changed them)
    length_mode = st.session_state.get("step2_length_mode", "Recommended")
    lyrics_label = st.session_state.get("step2_lyrics", "Allow lyrics")
    taste_label = st.session_state.get("step2_taste", "top10")
    exploration = int(st.session_state.get("step2_exploration", 25))
    use_recs = bool(st.session_state.get("step2_recs", True))
    dry_run = bool(st.session_state.get("step2_dry_run", False))

    min_hours: float | None = rec_hours
    tracks_overall: int | None = None
    if length_mode == "Recommended":
        min_hours = rec_hours
    elif length_mode == "Custom hours":
        min_hours = float(st.session_state.get("step2_custom_hours", rec_hours))
    elif length_mode == "Track count instead":
        min_hours = 0.0
        tracks_overall = int(st.session_state.get("step2_track_count", 20))
    else:
        min_hours = 0.0

    # ── Generate using existing pipeline (web = overall only) ────────────
    mode = Mode.OVERALL
    lyrics = _map_lyrics(lyrics_label)
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
        min_popularity=max(40, get_settings().chapterscore_min_popularity),
    )

    need_spotify = not dry_run
    missing = settings.missing_required(need_spotify=need_spotify, need_xai=True)
    if missing:
        st.error(
            "Missing configuration: "
            + ", ".join(missing)
            + ". Add them to Streamlit Secrets or `.env`."
        )
        return

    if need_spotify and not session_is_authenticated(st.session_state):
        st.error(
            "Please **Login with Spotify** first (sidebar) to create a real playlist. "
            "Or enable **Dry run**."
        )
        return

    sp = None
    if need_spotify:
        try:
            sp = get_session_spotify(st.session_state)
        except ChapterScoreError as exc:
            st.error(_friendly_error(exc))
            return

    status = st.status("Generating playlist…", expanded=True)

    def progress(msg: str) -> None:
        status.update(label=msg[:80], state="running")
        status.write(f"▸ {msg}")

    try:
        # Reuse full pipeline; identity from confirmed Stage 1 candidate
        result = generate_playlist(
            book.title,
            author=book.authors[0] if book.authors else (
                st.session_state.get(SS_LOOKUP_AUTHOR) or None
            ),
            isbn=book.isbn,
            mode=mode,
            lyrics=lyrics,
            tracks=int(tracks_overall) if tracks_overall else None,
            min_hours=float(min_hours) if min_hours and min_hours > 0 else 0.0,
            public=False,
            dry_run=dry_run,
            use_cache=True,
            progress=progress,
            spotify_client=sp,
            personalization=prefs,
        )
        status.update(label="Done", state="complete")
    except TypeError as exc:
        if "spotify_client" in str(exc):
            status.update(label="Deploy outdated", state="error")
            st.error(
                "This deployment is running an **old** `generate_playlist` without "
                "`spotify_client` support.\n\n"
                f"Loaded pipeline: `{generate_playlist.__code__.co_filename}`\n"
                f"ChapterScore version: **{__version__}**\n\n"
                "Push the latest code and reboot the Streamlit Cloud app."
            )
            return
        status.update(label="Failed", state="error")
        st.error(_friendly_error(exc))
        return
    except ChapterScoreError as exc:
        status.update(label="Failed", state="error")
        st.error(_friendly_error(exc))
        return
    except Exception as exc:
        status.update(label="Failed", state="error")
        st.error(_friendly_error(exc))
        with st.expander("Technical details"):
            st.exception(exc)
        return

    # Snapshot for result panel (session-safe primitives).
    # Extra vibe/settings fields feed the copy-feedback template only.
    requested_hours = result.requested_hours
    if not requested_hours and min_hours and float(min_hours) > 0:
        requested_hours = float(min_hours)
    snap: dict[str, Any] = {
        "result_id": uuid.uuid4().hex,
        "dry_run": dry_run,
        "mode": mode.value,
        "book_title": result.book.display_name,
        "title": result.book.title,
        "author": result.book.author_str,
        "lyrics": lyrics.display_label,
        "exploration": int(exploration),
        "top_artists": taste_label,
        "recommendations": bool(use_recs),
        "target_tracks": int(tracks_overall) if tracks_overall else None,
        "mood": result.analysis.overall_mood,
        "energy": result.analysis.overall_energy,
        "intimacy": result.analysis.intimacy_vs_epic,
        "atmospheres": list(result.analysis.atmospheres or []),
        "era": result.analysis.era_feel or "",
        "signature": result.analysis.distinctive_signature or "",
        "description": result.analysis.playlist_description or "",
        "playlist_title": result.analysis.playlist_title_suggestion or "",
        "styles": list(result.analysis.suitable_styles or [])[:8],
        "avoid": list(result.analysis.avoid_styles or [])[:8],
        "requested_hours": requested_hours,
        "actual_hours": result.actual_hours,
        "duration_report": result.duration_report,
        "tracks": [
            {
                "name": t.name,
                "artists": t.artist_str,
                "chapter": t.chapter_number,
            }
            for t in (result.tracks or [])
        ],
        "playlist": None,
    }
    if result.playlist:
        snap["playlist"] = {
            "name": result.playlist.name,
            "url": result.playlist.url,
            "track_count": result.playlist.track_count,
        }
    st.session_state[SS_RESULT] = snap
    st.rerun()



def _render_results() -> None:
    snap = st.session_state.get(SS_RESULT)
    if not snap:
        return

    dry = bool(snap.get("dry_run"))
    st.success("Generation complete" + (" (dry run)" if dry else ""))

    st.markdown("##### Book & vibe")
    m1, m2, m3 = st.columns(3)
    m1.metric("Mood", snap.get("mood") or "—")
    energy = snap.get("energy")
    m2.metric("Energy", f"{energy:.2f}" if isinstance(energy, (int, float)) else "—")
    m3.metric("Mode", snap.get("mode") or "—")

    if snap.get("signature"):
        st.markdown(f"**Signature:** {snap['signature']}")
    if snap.get("description"):
        st.info(snap["description"])
    if snap.get("styles") or snap.get("avoid"):
        c1, c2 = st.columns(2)
        if snap.get("styles"):
            c1.markdown("**Styles:** " + ", ".join(snap["styles"]))
        if snap.get("avoid"):
            c2.markdown("**Avoid:** " + ", ".join(snap["avoid"]))
    st.caption(f"**{snap.get('book_title', '—')}**")

    # Requested vs actual duration
    req_h = snap.get("requested_hours")
    act_h = snap.get("actual_hours")
    if req_h or act_h or snap.get("duration_report"):
        st.markdown("##### Duration")
        d1, d2, d3 = st.columns(3)
        d1.metric(
            "Requested",
            f"{req_h:g} h" if isinstance(req_h, (int, float)) and req_h else "—",
        )
        d2.metric(
            "Actual",
            f"{act_h:.2f} h" if isinstance(act_h, (int, float)) and act_h else "—",
        )
        if isinstance(req_h, (int, float)) and req_h and isinstance(act_h, (int, float)):
            pct = (act_h / req_h) * 100.0
            d3.metric("Fill", f"{pct:.0f}%")
        if snap.get("duration_report"):
            st.caption(snap["duration_report"])
        if (
            isinstance(req_h, (int, float))
            and req_h
            and isinstance(act_h, (int, float))
            and act_h < req_h * 0.88
        ):
            st.warning(
                "Under the requested duration — in-style pool exhausted. "
                "Quality and setting fit were preserved (no mismatched cinema padding)."
            )

    if dry:
        st.markdown("##### Would create")
        st.write(f"**{snap.get('playlist_title') or 'ChapterScore playlist'}**")
        if snap.get("description"):
            st.write(snap["description"])
        st.caption("Uncheck **Dry run** and generate again to create a real playlist.")
    else:
        pl = snap.get("playlist")
        if pl:
            st.markdown("##### Your playlist")
            dur_bits = f"{pl.get('track_count', 0)} tracks"
            if isinstance(act_h, (int, float)) and act_h:
                dur_bits += f" · {act_h:.2f} h"
            st.markdown(
                f"""
                <div class="result-card">
                  <div style="font-size:1.25rem;font-weight:700;margin-bottom:0.35rem;">
                    {pl.get("name", "Playlist")}
                  </div>
                  <div class="cs-result-muted" style="color:var(--cs-result-muted,#6b6b6b);margin-bottom:0.75rem;">
                    {dur_bits}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if pl.get("url"):
                st.link_button("Open Spotify playlist", pl["url"], use_container_width=True)

        tracks = snap.get("tracks") or []
        if tracks:
            with st.expander(f"Track list ({len(tracks)})", expanded=False):
                for i, t in enumerate(tracks, 1):
                    ch = f" · ch {t['chapter']}" if t.get("chapter") is not None else ""
                    st.write(f"{i}. **{t.get('name', '?')}** — {t.get('artists', '')}{ch}")

    _render_copy_feedback(snap)


# Clipboard writes have to happen in the browser click, not on a Streamlit rerun.
_COPY_FEEDBACK_HTML = """
<div>
  <button type="button" id="cs-copy-feedback"
          style="width:100%;font-weight:600;padding:0.65rem 1rem;background:#000;color:#fff;border:1px solid #000;border-radius:0.35rem;">
    Copy feedback
  </button>
  <p id="cs-copy-feedback-status" role="status" style="min-height:1.25rem;margin:0.35rem 0 0;color:#444;"></p>
</div>
<script>
(function () {
  const btn = document.getElementById("cs-copy-feedback");
  const status = document.getElementById("cs-copy-feedback-status");
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";

  // Colors come from #cs-copy-feedback CSS (theme vars) — do not copy
  // computed primary styles (Streamlit Dark menu can wash them to white-on-white).
  btn.style.width = "100%";
  btn.style.cursor = "pointer";
  btn.style.boxSizing = "border-box";

  function findArea() {
    const blocks = document.querySelectorAll('[data-testid="stTextArea"]');
    for (const block of blocks) {
      const label = block.querySelector("label");
      const text = label ? label.textContent || "" : "";
      if (text.indexOf("Feedback template") !== -1) {
        const area = block.querySelector("textarea");
        if (area) return area;
      }
    }
    return document.querySelector('textarea[aria-label="Feedback template"]');
  }

  function show(msg) {
    if (status) status.textContent = msg;
  }

  function fallback(area) {
    try {
      if (!area) return false;
      const start = area.selectionStart;
      const end = area.selectionEnd;
      area.focus();
      area.select();
      const ok = document.execCommand("copy");
      if (typeof start === "number" && typeof end === "number") {
        area.setSelectionRange(start, end);
      }
      return ok;
    } catch (err) {
      return false;
    }
  }

  let copiedAt = 0;
  function copyNow() {
    const now = Date.now();
    if (copiedAt && now - copiedAt < 400) return;
    copiedAt = now;
    const area = findArea();
    const text = area ? area.value : "";
    const fail = "Could not copy. Select the text and copy it manually.";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { show("Copied"); },
        function () { show(fallback(area) ? "Copied" : fail); }
      );
      return;
    }
    show(fallback(area) ? "Copied" : fail);
  }

  // Keep the textarea focused so editing does not rerun the app before copy.
  btn.addEventListener("mousedown", function (event) {
    event.preventDefault();
    copyNow();
  });
  btn.addEventListener("click", copyNow);
})();
</script>
"""


def _feedback_source_id(snap: dict[str, Any]) -> str:
    result_id = snap.get("result_id")
    if result_id:
        return str(result_id)
    tracks = snap.get("tracks") or []
    names = "|".join(
        f"{t.get('name', '')}\t{t.get('artists', '')}"
        for t in tracks
        if isinstance(t, dict)
    )
    return "|".join(
        [
            str(snap.get("title") or snap.get("book_title") or ""),
            str(snap.get("requested_hours")),
            str(snap.get("actual_hours")),
            str(len(tracks) if isinstance(tracks, list) else 0),
            names,
        ]
    )


@st.fragment
def _render_copy_feedback(snap: dict[str, Any]) -> None:
    """Editable feedback template plus a button that copies the current text."""
    source_id = _feedback_source_id(snap)
    if st.session_state.get(SS_FEEDBACK_FOR) != source_id:
        st.session_state[SS_FEEDBACK] = build_feedback_template(snap)
        st.session_state[SS_FEEDBACK_FOR] = source_id

    with st.container(border=True):
        st.markdown("##### Copy feedback")
        st.caption(
            "Edit the template, then copy it. "
            "Nothing here is sent to the model."
        )
        st.text_area(
            "Feedback template",
            key=SS_FEEDBACK,
            height=420,
            help="Replace each ? with KEEP or REJECT and add a short reason.",
        )
        st.html(_COPY_FEEDBACK_HTML, unsafe_allow_javascript=True)


def main() -> None:
    load_streamlit_secrets_into_env()
    reload_settings()
    settings = get_settings()

    _handle_oauth_redirect()

    # Light B&W only — dark mode toggle parked (CSS via st.html, not markdown)
    _inject_theme_css()

    if st.session_state.pop("auth_flash_ok", None):
        st.toast("Spotify connected", icon="✅")
    flash_err = st.session_state.pop("auth_flash_error", None)
    if flash_err:
        st.error(flash_err)

    st.markdown("# ChapterScore")

    st.markdown(
        '<p class="subtitle">Every book deserves its own soundtrack</p>',
        unsafe_allow_html=True,
    )

    _render_sidebar(settings)

    if not session_is_authenticated(st.session_state):
        st.info(
            "Connect Spotify to create playlists. Step 1 book analysis works without login."
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

    # Progress indicator
    step1_done = bool(st.session_state.get(SS_STEP1_DONE))
    p1, p2 = st.columns(2)
    p1.markdown("**① Book** " + ("✅" if step1_done else "…"))
    p2.markdown("**② Personalize** " + ("✅ unlocked" if step1_done else "🔒 locked"))

    _render_step1(settings)
    _render_step2(settings)
    _render_results()


if __name__ == "__main__":
    main()
