"""Plain-text playlist feedback for the web UI.

Built from the result snapshot already on screen. The text is for the user
to edit and copy. It is never sent to a model.
"""

from __future__ import annotations

from typing import Any

_TASTE_LABELS = {
    "disable": "off",
    "top5": "top 5",
    "top10": "top 10",
    "top15": "top 15",
}

_INSTRUCTION = "Replace each ? with KEEP or REJECT and add a short reason."


def build_feedback_template(snap: dict[str, Any] | None) -> str:
    """Return the editable feedback template for one generated result."""
    data = snap or {}
    tracks = _track_lines(data.get("tracks"))
    return (
        f"Book: {_book_title(data)}\n"
        f"Author: {_line(data.get('author'), fallback='Unknown')}\n"
        f"Settings: {_settings_line(data)}\n"
        f"Vibe panel: {_vibe_line(data)}\n"
        "Wanted world:\n"
        "\n"
        f"Requested vs actual: {_duration_line(data)}\n"
        "\n"
        "Tracks:\n"
        f"{tracks}\n"
        "\n"
        f"{_INSTRUCTION}\n"
    )


def _book_title(data: dict[str, Any]) -> str:
    if data.get("title"):
        return _line(data.get("title"))
    return _line(data.get("book_title"))


def _settings_line(data: dict[str, Any]) -> str:
    hours = _hours_number(data.get("requested_hours"))
    taste_raw = _line(data.get("top_artists"), fallback="")
    taste = _TASTE_LABELS.get(taste_raw, taste_raw or "—")
    recs = data.get("recommendations")
    if recs is True:
        rec_txt = "on"
    elif recs is False:
        rec_txt = "off"
    else:
        rec_txt = "—"
    exploration = data.get("exploration")
    if isinstance(exploration, (int, float)) and not isinstance(exploration, bool):
        exp_txt = str(int(exploration))
    else:
        exp_txt = "—"
    parts = [
        f"lyrics: {_line(data.get('lyrics'))}",
        f"requested hours: {hours if hours is not None else 'none'}",
        f"exploration: {exp_txt}",
        f"Top Artists: {taste}",
        f"recommendations: {rec_txt}",
    ]
    target = data.get("target_tracks")
    if isinstance(target, int) and not isinstance(target, bool) and target > 0 and hours is None:
        parts.insert(2, f"target tracks: {target}")
    return " · ".join(parts)


def _vibe_line(data: dict[str, Any]) -> str:
    return (
        f"mood: {_line(data.get('mood'))} · "
        f"energy: {_score(data.get('energy'))} · "
        f"intimacy: {_score(data.get('intimacy'))} · "
        f"atmospheres: {_csv(data.get('atmospheres'))} · "
        f"era: {_line(data.get('era'))} · "
        f"suitable styles: {_csv(data.get('styles'))} · "
        f"avoid styles: {_csv(data.get('avoid'))}"
    )


def _duration_line(data: dict[str, Any]) -> str:
    requested = _hours_number(data.get("requested_hours"))
    actual = data.get("actual_hours")
    if isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual > 0:
        actual_txt = f"{float(actual):.2f} h"
    else:
        actual_txt = "—"
    requested_txt = f"{requested} h" if requested is not None else "—"
    count = _track_count(data)
    noun = "track" if count == 1 else "tracks"
    return f"requested {requested_txt} · actual {actual_txt} · {count} {noun}"


def _track_count(data: dict[str, Any]) -> int:
    tracks = data.get("tracks")
    if isinstance(tracks, list) and tracks:
        return sum(1 for t in tracks if isinstance(t, dict))
    playlist = data.get("playlist")
    if isinstance(playlist, dict):
        count = playlist.get("track_count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return count
    return 0


def _track_lines(tracks: Any) -> str:
    if not isinstance(tracks, list) or not tracks:
        return "(none)"
    lines: list[str] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        title = _line(track.get("name"), fallback="Untitled")
        artist = _line(track.get("artists"), fallback="Unknown")
        lines.append(f"? — {title} — {artist}")
    return "\n".join(lines) if lines else "(none)"


def _hours_number(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return f"{float(value):g}"


def _score(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):.2f}"


def _csv(value: Any) -> str:
    if isinstance(value, str):
        return _line(value)
    if isinstance(value, (list, tuple)):
        parts = [_line(item, fallback="") for item in value]
        parts = [part for part in parts if part]
        return ", ".join(parts) if parts else "—"
    return "—"


def _line(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = " ".join(str(value).split())
    return text or fallback
