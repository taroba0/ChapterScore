"""Plain-text copy-feedback template built from a playlist snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

from feedback import build_feedback_template  # noqa: E402


def test_template_shape_from_on_screen_result():
    snap = {
        "title": "East of Eden",
        "author": "John Steinbeck",
        "lyrics": "Allow lyrics",
        "requested_hours": 2.4,
        "exploration": 25,
        "top_artists": "top10",
        "recommendations": True,
        "mood": "somber",
        "energy": 0.35,
        "intimacy": 0.8,
        "atmospheres": ["dust", "heat"],
        "era": "1910s Salinas",
        "styles": ["folk", "chamber"],
        "avoid": ["EDM", "trap"],
        "actual_hours": 2.18,
        "tracks": [
            {"name": "Song", "artists": "Artist"},
            {"name": "Other", "artists": "Someone"},
        ],
    }
    expected = (
        "Book: East of Eden\n"
        "Author: John Steinbeck\n"
        "Settings: lyrics: Allow lyrics · requested hours: 2.4 · "
        "exploration: 25 · Top Artists: top 10 · recommendations: on\n"
        "Vibe panel: mood: somber · energy: 0.35 · intimacy: 0.80 · "
        "atmospheres: dust, heat · era: 1910s Salinas · "
        "suitable styles: folk, chamber · avoid styles: EDM, trap\n"
        "Wanted world:\n"
        "\n"
        "Requested vs actual: requested 2.4 h · actual 2.18 h · 2 tracks\n"
        "\n"
        "Tracks:\n"
        "? — Song — Artist\n"
        "? — Other — Someone\n"
        "\n"
        "Replace each ? with KEEP or REJECT and add a short reason.\n"
    )
    assert build_feedback_template(snap) == expected


def test_track_count_mode_and_recommendations_off():
    text = build_feedback_template(
        {
            "title": "Dune",
            "author": "Frank Herbert",
            "lyrics": "Instrumental only",
            "requested_hours": 0,
            "target_tracks": 20,
            "exploration": 100,
            "top_artists": "disable",
            "recommendations": False,
            "mood": "epic",
            "energy": 0.7,
            "intimacy": 0,
            "atmospheres": [],
            "era": "",
            "styles": ["orchestral"],
            "avoid": ["pop"],
            "actual_hours": 1.5,
            "tracks": [{"name": "Spice", "artists": "Hans Zimmer"}],
        }
    )
    assert "lyrics: Instrumental only" in text
    assert "requested hours: none" in text
    assert "target tracks: 20" in text
    assert "exploration: 100" in text
    assert "Top Artists: off" in text
    assert "recommendations: off" in text
    assert "intimacy: 0.00" in text
    assert "atmospheres: —" in text
    assert "era: —" in text
    assert "requested — · actual 1.50 h · 1 track" in text
    assert "? — Spice — Hans Zimmer" in text
    assert "KEEP —" not in text
    assert "REJECT —" not in text


def test_every_track_is_listed_and_newlines_are_flattened():
    tracks = [
        {"name": "One\nTwo", "artists": "A  B"},
        {"name": "Three", "artists": ""},
        "skip-me",
    ]
    text = build_feedback_template({"title": "Book", "tracks": tracks})
    assert "? — One Two — A B" in text
    assert "? — Three — Unknown" in text
    assert text.count("? — ") == 2


def test_missing_fields_still_leave_a_blank_wanted_world():
    text = build_feedback_template({})
    assert text.startswith("Book: —\nAuthor: Unknown\n")
    assert "Wanted world:\n\nRequested vs actual:" in text
    assert "Tracks:\n(none)\n" in text
    assert text.endswith("Replace each ? with KEEP or REJECT and add a short reason.\n")


def test_old_snapshot_without_new_fields():
    text = build_feedback_template(
        {
            "book_title": "Dune by Frank Herbert",
            "mood": "epic",
            "energy": 0.7,
            "styles": ["orchestral"],
            "avoid": ["pop"],
            "requested_hours": 1.5,
            "actual_hours": 1.2,
            "tracks": [{"name": "Spice", "artists": "Hans"}],
        }
    )
    assert "Book: Dune by Frank Herbert" in text
    assert "Author: Unknown" in text
    assert "intimacy: —" in text
    assert "Top Artists: —" in text
    assert "? — Spice — Hans" in text


def test_results_panel_prefills_and_keeps_edits():
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    snap = {
        "result_id": "result-a",
        "title": "East of Eden",
        "author": "John Steinbeck",
        "book_title": "East of Eden by John Steinbeck",
        "lyrics": "Prefer instrumental",
        "requested_hours": 1.5,
        "exploration": 40,
        "top_artists": "top5",
        "recommendations": True,
        "mood": "warm",
        "energy": 0.4,
        "intimacy": 0.9,
        "atmospheres": ["family"],
        "era": "Salinas",
        "styles": ["folk"],
        "avoid": ["edm"],
        "actual_hours": 1.25,
        "dry_run": False,
        "tracks": [
            {"name": "River", "artists": "Ada"},
            {"name": "Field", "artists": "Ben"},
        ],
        "playlist": {
            "name": "East of Eden",
            "url": "https://open.spotify.com/playlist/example",
            "track_count": 2,
        },
    }
    # Login button needs a client id when the session is signed out.
    # setdefault leaves a real local .env value alone.
    import os

    os.environ.setdefault("SPOTIFY_CLIENT_ID", "test-client-id")
    os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test-client-secret")

    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "web" / "app.py",
        default_timeout=60,
    )
    app.session_state["cs_last_result"] = snap
    app.run()
    assert not app.exception, app.exception

    areas = [area for area in app.text_area if area.label == "Feedback template"]
    assert len(areas) == 1
    draft = areas[0].value or ""
    assert "Book: East of Eden" in draft
    assert "Author: John Steinbeck" in draft
    assert "lyrics: Prefer instrumental" in draft
    assert "Top Artists: top 5" in draft
    assert "recommendations: on" in draft
    assert "intimacy: 0.90" in draft
    assert "? — River — Ada" in draft
    assert "? — Field — Ben" in draft
    assert "Wanted world:\n" in draft

    edited = draft.replace("? — River — Ada", "KEEP — River — Ada — fits the farm")
    areas[0].set_value(edited)
    app.run()
    kept = [area for area in app.text_area if area.label == "Feedback template"][0].value
    assert "KEEP — River — Ada — fits the farm" in (kept or "")
    assert "? — Field — Ben" in (kept or "")

    # A new generation replaces the draft. An edit does not.
    snap2 = dict(snap)
    snap2["result_id"] = "result-b"
    snap2["tracks"] = [{"name": "New", "artists": "Cara"}]
    app.session_state["cs_last_result"] = snap2
    app.run()
    refreshed = [area for area in app.text_area if area.label == "Feedback template"][0].value or ""
    assert "? — New — Cara" in refreshed
    assert "KEEP — River" not in refreshed

    # No result, no feedback box.
    del app.session_state["cs_last_result"]
    app.run()
    assert not any(area.label == "Feedback template" for area in app.text_area)
    # Touch streamlit so the importorskip stays meaningful if AppTest moves.
    assert streamlit.__version__
