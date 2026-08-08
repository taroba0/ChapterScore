"""Prompt templates for deep literary vibe analysis (literature-first)."""

from __future__ import annotations

from chapterscore.models import BookMetadata, LyricsPreference, Mode

# ── Pass 1: pure literary reading (no music language) ───────────────────────

LITERARY_SYSTEM = """\
You are a careful literary critic and close reader. You analyze novels from
public plot summaries, publisher blurbs, critical reception language, themes
sections, and subject tags — never inventing fake plot facts, but you MAY
infer tone, voice, and emotional texture that those sources imply.

Your job is to produce a SPECIFIC profile of THIS book that would distinguish
it from other books in the same broad genre.

Anti-generic rules (mandatory):
- Never collapse a book into a genre template (e.g. all dystopias are not
  "dark epic cinematic"; all literary fiction is not "melancholic piano").
- Explicitly name what makes THIS book different from typical peers.
- Prefer concrete textures (voice, humor, intimacy scale, setting feel)
  over vague adjectives like "emotional" or "powerful" alone.
- If sources are thin, say so in distinctive_signature and stay conservative.

Respond with a single valid JSON object only. No markdown fences.
"""


def build_literary_prompt(book: BookMetadata, *, mode: Mode) -> str:
    context = book.analysis_context_blob(max_chars=16000)
    chapter_note = ""
    if mode == Mode.CHAPTER:
        if book.chapters and not book.raw.get("synthetic_chapters"):
            lines = []
            for ch in book.chapters[:40]:
                title = f" — {ch.title}" if ch.title else ""
                lines.append(f"  - Ch {ch.number}{title}")
            chapter_note = "Known chapter headings:\n" + "\n".join(lines)
        else:
            chapter_note = (
                "Chapter list is missing or synthetic. Prefer 4–6 major "
                "emotional ACTS (beginning, rising, turning points, climax, "
                "aftermath) rather than inventing fake chapter titles."
            )

    return f"""\
Produce a literature-first profile for this book as JSON.

BOOK IDENTITY
Title: {book.title}
Author(s): {book.author_str}
ISBN: {book.isbn or "n/a"}
Year: {book.publish_year or "n/a"}
Pages: {book.page_count or "n/a"}
Genre labels: {", ".join(book.genre_labels[:12]) or "n/a"}
Sources: {book.source}

PUBLIC MATERIALS (summaries / reception — not the full novel)
{context or "(minimal sources)"}

{chapter_note}

OUTPUT JSON SCHEMA (literary only — no Spotify queries yet):
{{
  "book_title": string,
  "authors": [string],
  "overall_mood": string,  // specific, not generic
  "overall_energy": number 0-1,
  "atmospheres": [string],  // multi-label emotional climates
  "emotional_arc": string,  // how feeling moves across the whole book
  "pacing": "slow" | "moderate" | "fast",
  "pacing_profile": string,  // how pacing changes (e.g. "slow burn then propulsive final act")
  "tone": string,
  "dominant_tones": [string],
  "secondary_tones": [string],
  "narrative_voice": string,  // wry, earnest, intimate, detached, sarcastic, polyphonic...
  "writing_style": string,  // spare, lyrical, dialogue-forward, experimental...
  "humor_level": number 0-1,
  "sarcasm_irony_level": number 0-1,
  "intimacy_vs_epic": number 0-1,  // 0=epic/sweeping, 1=intimate/personal
  "realism_vs_dreaminess": number 0-1,  // 0=gritty realism, 1=dreamy/surreal
  "era_feel": string,
  "setting_texture": string,  // time, place, social atmosphere in sensory terms
  "sensory_atmosphere": string,  // light, weather, sound, body-feeling of the world
  "key_themes": [string],
  "distinctive_signature": string,  // REQUIRED: what makes THIS book feel unique
  "genre_peers_contrast": string,  // REQUIRED: vs typical peers in same genre
  "anti_generic_notes": [string],  // e.g. "NOT epic battle music", "NOT pure dystopian grimdark"
  "emotional_acts": [  // 4-6 acts if chapter data is weak; else may be empty
    {{
      "act_id": number | string,
      "label": string,
      "mood": string,
      "energy_level": number 0-1,
      "atmospheres": [string],
      "emotional_arc": string,
      "pacing": "slow" | "moderate" | "fast",
      "tone": string,
      "vibe_note": string
    }}
  ]
}}

Be precise. JSON only.
"""


# ── Pass 2: music mapping from literary profile ─────────────────────────────

MUSIC_SYSTEM = """\
You are a literary music supervisor. You receive a finished literary profile
of a novel and must translate it into Spotify-ready search queries and style
guidance.

Rules:
- LITERATURE FIRST: every musical choice must be justified by the literary
  profile (voice, intimacy scale, humor, setting, distinctive signature).
- ANTI-GENERIC: do not default to "dark epic cinematic" or "melancholic piano"
  unless the profile truly warrants it. Honor anti_generic_notes strictly.
- suitable_styles must be specific (e.g. "bittersweet neoclassical piano",
  "wry indie folk", "lo-fi nostalgic ambient") not just "cinematic".
- avoid_styles must block real mismatches (epic trailer music for intimate
  contemporary literary fiction; country for cyberpunk, etc.).
- Search queries: 2–6 words, Spotify-friendly, artist-free unless iconic.
- energy/valence 0–1 on Spotify-like scales.
- JSON only, no markdown.
"""


def build_music_prompt(
    book: BookMetadata,
    literary: dict,
    *,
    mode: Mode,
    lyrics: LyricsPreference,
) -> str:
    lyrics = lyrics.normalized()
    lyrics_instruction = {
        LyricsPreference.ALLOW_LYRICS: (
            "Vocals allowed. Match voice/tone of the prose; instrumentalness low."
        ),
        LyricsPreference.PREFER_INSTRUMENTAL: (
            "Prefer instrumental/score-leaning music but vocals OK. "
            "instrumentalness_min ~0.45–0.6 when useful."
        ),
        LyricsPreference.INSTRUMENTAL_ONLY: (
            "STRICT instrumental / soundtrack-style only. No sung vocals. "
            "instrumentalness_min ≥ 0.75. Still match intimacy scale — "
            "intimate books need intimate instrumental, NOT epic trailer scores."
        ),
    }[lyrics]

    import json

    lit_json = json.dumps(literary, ensure_ascii=False, indent=2)
    if len(lit_json) > 12000:
        lit_json = lit_json[:12000] + "\n…"

    chapter_schema = ""
    if mode == Mode.CHAPTER:
        chapter_schema = """
  "chapters": [
    {
      "chapter_number": number | string,
      "chapter_title": string | null,
      "mood": string,
      "energy_level": number 0-1,
      "atmospheres": [string],
      "emotional_arc": string,
      "key_scenes": [string],
      "pacing": "slow" | "moderate" | "fast",
      "tone": string,
      "vibe_note": string,
      "suggested_genres": [string],
      "avoid": [string],
      "search_queries": [ { "query": string, "genres": [string], "mood_keywords": [string],
        "energy": number|null, "valence": number|null, "tempo_bpm": number|null,
        "instrumentalness_min": number|null, "acousticness": number|null,
        "danceability": number|null, "reason": string } ]
    }
  ],
"""
    else:
        chapter_schema = '  "chapters": [],\n'

    return f"""\
Map this literary profile into music direction for Spotify.

BOOK: {book.title} by {book.author_str}
MODE: {mode.value}
LYRICS: {lyrics.value}
{lyrics_instruction}

LITERARY PROFILE (source of truth):
{lit_json}

OUTPUT JSON — copy literary fields forward and ADD music fields:
{{
  "book_title": string,
  "authors": [string],
  "overall_mood": string,
  "overall_energy": number,
  "atmospheres": [string],
  "emotional_arc": string,
  "pacing": string,
  "pacing_profile": string,
  "tone": string,
  "dominant_tones": [string],
  "secondary_tones": [string],
  "narrative_voice": string,
  "writing_style": string,
  "humor_level": number,
  "sarcasm_irony_level": number,
  "intimacy_vs_epic": number,
  "realism_vs_dreaminess": number,
  "era_feel": string,
  "setting_texture": string,
  "sensory_atmosphere": string,
  "key_themes": [string],
  "distinctive_signature": string,
  "genre_peers_contrast": string,
  "anti_generic_notes": [string],
  "emotional_acts": [ ... same as literary, may add search_queries per act ... ],
{chapter_schema}
  "suggested_genres": [string],
  "suitable_styles": [string],  // 5-10 SPECIFIC styles from the literary profile
  "avoid_styles": [string],     // 5-10 hard mismatches including cliché traps
  "playlist_title_suggestion": string,
  "playlist_description": string,  // mention distinctive literary texture, not generic genre
  "overall_search_queries": [  // 6-12 queries; overall mode only (empty in chapter mode if chapters filled)
    {{
      "query": string,
      "genres": [string],
      "mood_keywords": [string],
      "energy": number | null,
      "valence": number | null,
      "tempo_bpm": number | null,
      "instrumentalness_min": number | null,
      "acousticness": number | null,
      "danceability": number | null,
      "reason": string  // must cite literary trait (voice/setting/signature)
    }}
  ]
}}

If mode is chapter and literary emotional_acts exist but chapters are empty/synthetic,
populate emotional_acts with search_queries and keep chapters empty OR map acts into chapters.
JSON only.
"""


# Backward-compatible names used by older imports / tests
SYSTEM_PROMPT = LITERARY_SYSTEM


def build_user_prompt(
    book: BookMetadata,
    *,
    mode: Mode,
    lyrics: LyricsPreference,
    max_chapters: int = 20,
) -> str:
    """Single-prompt fallback (also used by tests). Literature + music combined."""
    # Combined one-shot for tests and emergency fallback
    lit = build_literary_prompt(book, mode=mode)
    lyrics = lyrics.normalized()
    lyrics_note = {
        LyricsPreference.ALLOW_LYRICS: "Vocals allowed (WITH lyrics OK).",
        LyricsPreference.PREFER_INSTRUMENTAL: "Prefer instrumental; vocals OK.",
        LyricsPreference.INSTRUMENTAL_ONLY: "STRICT instrumental / soundtrack only (no vocals).",
    }.get(lyrics, f"Lyrics mode: {lyrics.value}")
    return (
        lit
        + f"\n\nMODE: {mode.value}\n"
        + f"{lyrics_note}\n"
        + "Also include music fields: suitable_styles, avoid_styles, "
        "suggested_genres, overall_search_queries (5-10), playlist_title_suggestion, "
        f"playlist_description. Lyrics mode: {lyrics.value}.\n"
        "Still put distinctive_signature and genre_peers_contrast first.\nJSON only.\n"
    )
