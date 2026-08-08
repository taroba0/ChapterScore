"""Prompt templates for literary vibe analysis."""

from __future__ import annotations

from chapterscore.models import BookMetadata, LyricsPreference, Mode

SYSTEM_PROMPT = """\
You are a literary music supervisor and soundtrack curator with deep knowledge of
fiction, film scores, and popular music. Your job is to translate a book's emotional
landscape into precise musical direction that a Spotify search can actually find.

You MUST respond with a single valid JSON object matching the schema described by the
user. No markdown fences, no commentary outside JSON.

Guidelines for search queries:
- Write queries a Spotify search box understands well (artist-free unless iconic).
- Mix concrete genre terms with mood adjectives (e.g. "dark ambient tension",
  "intimate acoustic folk ballad", "epic orchestral adventure").
- Prefer 2–5 word queries; avoid full sentences.
- For instrumental/soundtrack mode, bias toward film score, ambient, neoclassical,
  post-rock, classical, lo-fi instrumental — never pure pop vocal hooks.
- For lyrics-welcome mode, vocal songs are fine; still match mood tightly.
- energy and valence are 0.0–1.0 (Spotify audio-feature scale).
- instrumentalness_min: set ≥0.7 for instrumental-only, else omit or keep low.
- Suggest real, searchable genres (indie folk, trip hop, darkwave, orchestral,
  ambient, jazz noir, post-rock, chamber pop, etc.).
- Avoid generic queries like "sad song" or "happy music".
"""


def build_user_prompt(
    book: BookMetadata,
    *,
    mode: Mode,
    lyrics: LyricsPreference,
    max_chapters: int = 20,
) -> str:
    lyrics_instruction = {
        LyricsPreference.YES: (
            "User wants songs WITH lyrics/vocals. Prefer vocal tracks; "
            "instrumentalness_min should be omitted or very low."
        ),
        LyricsPreference.NO: (
            "User accepts either vocal or instrumental tracks. Mix freely; "
            "match mood first."
        ),
        LyricsPreference.INSTRUMENTAL_ONLY: (
            "User wants PURELY INSTRUMENTAL / soundtrack-style music only. "
            "No sung vocals. Set instrumentalness_min to at least 0.75 on every "
            "query. Prefer film scores, ambient, classical, post-rock instrumental, "
            "neoclassical, etc."
        ),
    }[lyrics]

    chapter_block = ""
    if mode == Mode.CHAPTER and book.chapters:
        lines = []
        for ch in book.chapters[:max_chapters]:
            title = f" — {ch.title}" if ch.title else ""
            summary = f"\n    Summary: {ch.summary}" if ch.summary else ""
            lines.append(f"  - Chapter {ch.number}{title}{summary}")
        chapter_block = "Known chapters:\n" + "\n".join(lines)
        if book.raw.get("synthetic_chapters"):
            chapter_block += (
                "\n(Note: chapter list is synthetic/estimated — invent plausible "
                "emotional arcs across the novel's structure based on the plot.)"
            )
    elif mode == Mode.CHAPTER:
        chapter_block = (
            "No chapter list available. Infer 8–12 narrative beats / arcs from the "
            "plot and treat each as a 'chapter' with sequential numbers."
        )

    plot = book.plot_summary or book.description or ""
    if len(plot) > 10000:
        plot = plot[:10000] + "\n…[truncated]"

    schema_overall = """
{
  "book_title": string,
  "authors": [string],
  "overall_mood": string,
  "overall_energy": number (0-1),
  "atmospheres": [string],  // e.g. calm, tense, romantic, epic, melancholic, eerie, triumphant, intimate, hopeful, dark, adventurous, nostalgic, mysterious, playful, solemn
  "emotional_arc": string,
  "pacing": "slow" | "moderate" | "fast",
  "tone": string,
  "era_feel": string,
  "key_themes": [string],
  "suggested_genres": [string],
  "playlist_title_suggestion": string,  // creative, ≤80 chars, include book title lightly
  "playlist_description": string,       // 1-3 sentences for Spotify description
  "overall_search_queries": [
    {
      "query": string,
      "genres": [string],
      "mood_keywords": [string],
      "energy": number | null,
      "valence": number | null,
      "tempo_bpm": number | null,
      "instrumentalness_min": number | null,
      "acousticness": number | null,
      "danceability": number | null,
      "reason": string
    }
  ],  // 5-10 diverse queries covering the book's arc
  "chapters": []  // empty for overall mode
}
"""

    schema_chapter = """
{
  "book_title": string,
  "authors": [string],
  "overall_mood": string,
  "overall_energy": number (0-1),
  "atmospheres": [string],
  "emotional_arc": string,
  "pacing": "slow" | "moderate" | "fast",
  "tone": string,
  "era_feel": string,
  "key_themes": [string],
  "suggested_genres": [string],
  "playlist_title_suggestion": string,
  "playlist_description": string,  // mention chapter-by-chapter structure; keep under 300 chars
  "overall_search_queries": [],    // empty in chapter mode
  "chapters": [
    {
      "chapter_number": number | string,
      "chapter_title": string | null,
      "mood": string,
      "energy_level": number (0-1),
      "atmospheres": [string],
      "emotional_arc": string,
      "key_scenes": [string],
      "pacing": "slow" | "moderate" | "fast",
      "tone": string,
      "vibe_note": string,   // ONE short sentence for playlist description
      "suggested_genres": [string],
      "avoid": [string],
      "search_queries": [   // 2-3 queries per chapter
        {
          "query": string,
          "genres": [string],
          "mood_keywords": [string],
          "energy": number | null,
          "valence": number | null,
          "tempo_bpm": number | null,
          "instrumentalness_min": number | null,
          "acousticness": number | null,
          "danceability": number | null,
          "reason": string
        }
      ]
    }
  ]
}
"""

    schema = schema_chapter if mode == Mode.CHAPTER else schema_overall

    return f"""\
Analyze this book and produce musical direction as JSON.

MODE: {mode.value}
LYRICS PREFERENCE: {lyrics.value}
{lyrics_instruction}

BOOK
----
Title: {book.title}
Author(s): {book.author_str}
ISBN: {book.isbn or "n/a"}
Year: {book.publish_year or "n/a"}
Subjects/genres: {", ".join(book.subjects[:15]) or "n/a"}
Page count: {book.page_count or "n/a"}
Sources: {book.source}

Description:
{book.description[:1500] if book.description else "n/a"}

Plot / synopsis:
{plot or "n/a"}

{chapter_block}

OUTPUT SCHEMA (JSON only):
{schema}

Produce {("one chapter object per known chapter (or inferred beat), each with 2-3 search_queries" if mode == Mode.CHAPTER else "5-10 overall_search_queries spanning the emotional journey")}.
Be specific and musically literate. JSON only.
"""
