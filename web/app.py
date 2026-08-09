"""
ChapterScore — Streamlit web UI with a guided 2-step flow.

  Step 1 — Fast book identity lookup + confirm (no Grok, no deep wiki)
  Step 2 — Personalize & generate (locked until Step 1 is confirmed;
            full literary vibe analysis runs here via generate_playlist)

Launch locally:
    streamlit run web/app.py

Deployed (Streamlit Community Cloud): set secrets + Redirect URIs (see README).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Allow `streamlit run web/app.py` without an editable install
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st

from chapterscore import __version__
from chapterscore.books.aggregator import lookup_book_quick
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
SS_BOOK = "cs_book_data"  # BookMetadata.model_dump() from quick lookup
SS_LOOKUP_TITLE = "cs_lookup_title"
SS_LOOKUP_AUTHOR = "cs_lookup_author"
SS_HAS_CHAPTERS = "cs_has_real_chapters"  # cheap chapter-list hint
SS_READING_HOURS = "cs_reading_hours"  # float, user-editable estimate
SS_RESULT = "cs_last_result"  # last generation snapshot for display


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
        padding-top: 2rem;
        padding-bottom: 3rem;
        max-width: 740px;
      }
      h1 {
        font-weight: 700 !important;
        letter-spacing: -0.02em;
        margin-bottom: 0.25rem !important;
      }
      .subtitle {
        color: #6b7280;
        font-size: 1.05rem;
        margin-bottom: 1.25rem;
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
      .step-card {
        border: 1px solid #e2e8f0;
        border-radius: 0.9rem;
        padding: 1.1rem 1.25rem 1.25rem;
        margin-bottom: 1rem;
        background: #ffffff;
      }
      .step-card.locked {
        opacity: 0.55;
        background: #f8fafc;
      }
      .step-badge {
        display: inline-block;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 0.35rem;
      }
      .step-badge.active { color: #7c3aed; }
      .step-badge.done { color: #059669; }
      .confirm-box {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 0.75rem;
        padding: 0.9rem 1rem;
        margin: 0.75rem 0;
      }
      footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
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


def has_chapter_data_hint(book: BookMetadata) -> bool:
    """
    Cheap Step 1 signal for enabling Chapter mode later.

    Uses either a real chapter list (if present) or the quick Wikipedia
    section-index hint from ``lookup_book_quick`` — never requires full plot download.
    """
    if book.chapters and len(book.chapters) >= 3 and not (book.raw or {}).get("synthetic_chapters"):
        return True
    return bool((book.raw or {}).get("quick_chapter_hint"))


def _reset_step1_state() -> None:
    for k in (
        SS_STEP1_DONE,
        SS_BOOK,
        SS_HAS_CHAPTERS,
        SS_READING_HOURS,
        SS_RESULT,
        "step1_reading_hours",
    ):
        st.session_state.pop(k, None)


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
        f'<div class="step-badge {badge}">Step 1 · Quick book lookup</div>'
        f"<h3 style='margin:0 0 0.75rem 0;font-size:1.2rem;'>Find your book</h3>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Fast identity check only (catalogue metadata). "
        "Full literary vibe analysis runs later when you generate the playlist."
    )

    # Widget state via keys only (avoid value= + key= conflicts)
    if "step1_title_input" not in st.session_state:
        st.session_state["step1_title_input"] = st.session_state.get(SS_LOOKUP_TITLE, "")
    if "step1_author_input" not in st.session_state:
        st.session_state["step1_author_input"] = st.session_state.get(SS_LOOKUP_AUTHOR, "")

    title = st.text_input(
        "Book title",
        placeholder="e.g. Dune",
        help="Required. Full title works best for metadata matching.",
        key="step1_title_input",
    )
    author = st.text_input(
        "Author (optional)",
        placeholder="e.g. Frank Herbert",
        key="step1_author_input",
    )

    c1, c2 = st.columns(2)
    with c1:
        lookup_clicked = st.button(
            "Look up book",
            type="primary",
            use_container_width=True,
            key="btn_lookup_book",
        )
    with c2:
        if confirmed or st.session_state.get(SS_BOOK):
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
            # Step 1 needs no xAI key — only public catalogue APIs
            st.session_state[SS_STEP1_DONE] = False
            st.session_state.pop(SS_RESULT, None)
            status = st.status("Looking up book…", expanded=True)
            try:
                status.write("▸ Open Library + Google Books (identity)…")
                book = lookup_book_quick(
                    title.strip(),
                    author=author.strip() or None,
                    use_cache=True,
                )
                status.write(f"▸ Found: {book.display_name}")
                if book.page_count:
                    status.write(f"▸ Pages: {book.page_count}")
                status.update(label="Book ready for confirmation", state="complete")

                st.session_state[SS_BOOK] = book.model_dump(mode="json")
                st.session_state[SS_LOOKUP_TITLE] = title.strip()
                st.session_state[SS_LOOKUP_AUTHOR] = (author or "").strip()
                st.session_state[SS_HAS_CHAPTERS] = has_chapter_data_hint(book)
                rh = estimate_reading_hours(book.page_count)
                st.session_state[SS_READING_HOURS] = rh
                st.session_state["step1_reading_hours"] = rh
                st.rerun()
            except ChapterScoreError as exc:
                status.update(label="Lookup failed", state="error")
                st.error(_friendly_error(exc))
            except Exception as exc:
                status.update(label="Lookup failed", state="error")
                st.error(_friendly_error(exc))
                with st.expander("Technical details"):
                    st.exception(exc)

    # ── Confirmation panel (after successful lookup) ─────────────────────
    if st.session_state.get(SS_BOOK):
        book = BookMetadata.model_validate(st.session_state[SS_BOOK])
        has_ch = bool(st.session_state.get(SS_HAS_CHAPTERS))

        st.markdown(
            '<div class="confirm-box">',
            unsafe_allow_html=True,
        )
        st.markdown("##### Is this the right book?")
        st.markdown(f"**{book.title}**")
        st.caption(
            f"by {book.author_str}"
            + (f" · {book.publish_year}" if book.publish_year else "")
            + (f" · {book.page_count} pages" if book.page_count else "")
            + f" · sources: {book.source or '—'}"
        )

        # Short catalogue blurb only (not Grok analysis)
        blurb = (book.publisher_blurb or book.description or "").strip()
        if blurb:
            st.write(blurb[:400] + ("…" if len(blurb) > 400 else ""))

        m1, m2 = st.columns(2)
        m1.metric("Pages", str(book.page_count) if book.page_count else "Unknown")
        m2.metric("Chapter list available", "Likely" if has_ch else "No / unknown")

        if has_ch:
            st.caption(
                "A public chapter/contents section was detected. "
                "Chapter mode will be available in Step 2."
            )
        else:
            st.caption(
                "No public chapter-list signal found (quick check). "
                "Chapter mode will be disabled — use Overall mode."
            )

        # Editable reading-time estimate
        if "step1_reading_hours" not in st.session_state:
            st.session_state["step1_reading_hours"] = float(
                st.session_state.get(SS_READING_HOURS)
                or estimate_reading_hours(book.page_count)
            )
        reading_hours = st.number_input(
            "Estimated reading time (hours)",
            min_value=0.5,
            max_value=48.0,
            step=0.5,
            help=(
                "Default from page count (~35 pages/hour). "
                "Edit freely — used only to recommend playlist length, not to force duration."
            ),
            key="step1_reading_hours",
        )
        st.session_state[SS_READING_HOURS] = float(reading_hours)
        rec = recommend_playlist_hours(float(reading_hours))
        st.caption(
            f"Suggested playlist length: **~{rec:g} hours** "
            f"(soft target — adjustable in Step 2)."
        )

        st.markdown("</div>", unsafe_allow_html=True)

        if not confirmed:
            if st.button(
                "This is correct — Continue to personalization",
                type="primary",
                use_container_width=True,
                key="btn_confirm_book",
            ):
                st.session_state[SS_STEP1_DONE] = True
                st.session_state[SS_READING_HOURS] = float(reading_hours)
                st.rerun()
        else:
            st.success(
                "Book confirmed. Step 2 is unlocked — full vibe analysis runs when you generate."
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

    if not unlocked:
        st.caption(
            "🔒 Complete Step 1 and confirm the book to unlock mode, length, "
            "lyrics, and personalization controls."
        )
        # Greyed-out preview of controls (disabled)
        st.selectbox(
            "Mode",
            options=["Overall", "Chapter"],
            index=0,
            disabled=True,
            key="locked_mode",
        )
        st.number_input(
            "Playlist length (hours, soft target)",
            min_value=0.0,
            max_value=6.0,
            value=1.5,
            disabled=True,
            key="locked_hours",
        )
        st.selectbox(
            "Lyrics preference",
            options=["Allow lyrics", "Prefer instrumental", "Instrumental only"],
            disabled=True,
            key="locked_lyrics",
        )
        st.selectbox(
            "Personal taste (Top Artists)",
            options=["disable", "top5", "top10", "top15"],
            index=2,
            disabled=True,
            key="locked_taste",
        )
        st.slider(
            "Comfort ↔ Explorative",
            0,
            100,
            25,
            disabled=True,
            key="locked_explore",
        )
        st.button(
            "Generate Playlist",
            type="primary",
            disabled=True,
            use_container_width=True,
            key="locked_generate",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # ── Unlocked controls ────────────────────────────────────────────────
    book = BookMetadata.model_validate(st.session_state[SS_BOOK])
    has_ch = bool(st.session_state.get(SS_HAS_CHAPTERS))
    reading_h = float(st.session_state.get(SS_READING_HOURS) or 6.0)
    rec_hours = recommend_playlist_hours(reading_h)

    st.caption(
        f"Book: **{book.display_name}** · "
        f"reading estimate **{reading_h:g} h** · "
        f"recommended playlist **~{rec_hours:g} h** (soft)"
    )
    st.caption(
        "Generating runs the full literary vibe analysis + Spotify search "
        "(deferred from Step 1 so lookup stays fast)."
    )

    # 1. Mode
    if has_ch:
        mode_label = st.radio(
            "1. Mode",
            options=["Overall", "Chapter"],
            index=0,
            horizontal=True,
            help=(
                "Overall = one cohesive emotional world (shuffle-friendly). "
                "Chapter = ordered by narrative sections."
            ),
            key="step2_mode",
        )
    else:
        mode_label = "Overall"
        st.radio(
            "1. Mode",
            options=["Overall"],
            index=0,
            horizontal=True,
            help="Only Overall is available for this book.",
            key="step2_mode_overall_only",
        )
        st.info(
            "Chapter mode is disabled because no usable chapter-by-chapter synopsis "
            "was found for this book in public sources."
        )

    # 2. Playlist length (soft)
    st.markdown("**2. Playlist length**")
    st.caption(
        f"Recommended **~{rec_hours:g} hours** from your reading-time estimate "
        f"({reading_h:g} h). This is a soft preference — quality matching wins over padding."
    )
    length_choice = st.radio(
        "Length preference",
        options=[
            f"Recommended (~{rec_hours:g} h)",
            "Custom hours",
            "Track count instead",
            "No length target",
        ],
        index=0,
        key="step2_length_choice",
        label_visibility="collapsed",
    )

    min_hours: float | None = rec_hours
    tracks_overall: int | None = None
    if length_choice.startswith("Recommended"):
        min_hours = rec_hours
    elif length_choice == "Custom hours":
        min_hours = st.number_input(
            "Target hours (soft)",
            min_value=0.5,
            max_value=6.0,
            value=float(rec_hours),
            step=0.25,
            key="step2_custom_hours",
        )
    elif length_choice == "Track count instead":
        min_hours = 0.0
        tracks_overall = st.number_input(
            "Target tracks (soft)",
            min_value=8,
            max_value=80,
            value=20,
            step=1,
            key="step2_track_count",
        )
    else:
        min_hours = 0.0

    # 3. Lyrics
    lyrics_label = st.selectbox(
        "3. Lyrics preference",
        options=["Allow lyrics", "Prefer instrumental", "Instrumental only"],
        index=0,
        help=(
            "Allow lyrics = vocals OK · Prefer instrumental = soft bias · "
            "Instrumental only = hard no-vocals filter (Top Artists still allowed)"
        ),
        key="step2_lyrics",
    )

    # 4. Taste / exploration / recommendations
    st.markdown("**4. Personalization**")
    st.caption(
        "Priority: **(1)** vocals policy → **(2)** book style → "
        "**(3)** exploration → **(4)** Top Artists (soft)."
    )
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
        help="Soft seeds from your Spotify listening — never overrides book vibe or lyrics rules.",
        key="step2_taste",
    )
    if lyrics_label == "Instrumental only" and taste_label != "disable":
        st.warning(
            "Note: many of your top artists have vocals, so results may be limited "
            "in **Instrumental only** mode. Top Artists stays enabled."
        )

    exploration = st.slider(
        "Comfort ↔ Explorative",
        min_value=0,
        max_value=100,
        value=25,
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

    use_recs = st.toggle(
        "Use Spotify Recommendations API",
        value=True,
        help=(
            "Seed Recommendations with book vibe (+ your artists when Top Artists is on). "
            "Falls back to search if unavailable."
        ),
        key="step2_recs",
    )

    dry_run = st.checkbox(
        "Dry run (analyze / search plan only — no Spotify playlist write)",
        value=False,
        key="step2_dry_run",
    )

    # 5. Generate
    st.markdown("**5. Generate**")
    gen_clicked = st.button(
        "Generate Playlist",
        type="primary",
        use_container_width=True,
        key="btn_generate",
    )

    st.markdown("</div>", unsafe_allow_html=True)

    if not gen_clicked:
        return

    # ── Generate using existing pipeline ─────────────────────────────────
    mode = Mode.CHAPTER if mode_label == "Chapter" and has_ch else Mode.OVERALL
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
        # Reuse full pipeline; book identity from Step 1 inputs
        result = generate_playlist(
            book.title,
            author=book.authors[0] if book.authors else (
                st.session_state.get(SS_LOOKUP_AUTHOR) or None
            ),
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

    # Snapshot for result panel (session-safe primitives)
    snap: dict[str, Any] = {
        "dry_run": dry_run,
        "mode": mode.value,
        "book_title": result.book.display_name,
        "mood": result.analysis.overall_mood,
        "energy": result.analysis.overall_energy,
        "signature": result.analysis.distinctive_signature or "",
        "description": result.analysis.playlist_description or "",
        "playlist_title": result.analysis.playlist_title_suggestion or "",
        "styles": list(result.analysis.suitable_styles or [])[:8],
        "avoid": list(result.analysis.avoid_styles or [])[:8],
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

    if dry:
        st.markdown("##### Would create")
        st.write(f"**{snap.get('playlist_title') or 'ChapterScore playlist'}**")
        if snap.get("description"):
            st.write(snap["description"])
        st.caption("Uncheck **Dry run** and generate again to create a real playlist.")
        return

    pl = snap.get("playlist")
    if pl:
        st.markdown("##### Your playlist")
        st.markdown(
            f"""
            <div class="result-card">
              <div style="font-size:1.25rem;font-weight:700;margin-bottom:0.35rem;">
                {pl.get("name", "Playlist")}
              </div>
              <div style="color:#64748b;margin-bottom:0.75rem;">
                {pl.get("track_count", 0)} tracks
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


def main() -> None:
    load_streamlit_secrets_into_env()
    reload_settings()
    settings = get_settings()

    _handle_oauth_redirect()

    if st.session_state.pop("auth_flash_ok", None):
        st.toast("Spotify connected", icon="✅")
    flash_err = st.session_state.pop("auth_flash_error", None)
    if flash_err:
        st.error(flash_err)

    st.markdown("# ChapterScore")
    st.markdown(
        '<p class="subtitle">Soundtracks for the books you love — in two easy steps</p>',
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
