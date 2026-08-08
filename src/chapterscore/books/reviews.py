"""Public review / reception language (legal sources only).

We do not scrape Goodreads or paywalled reviews. Instead we extract
tone-rich language from:
  - Wikipedia Reception sections (via caller)
  - Open Library first-sentence subjects / descriptions already in metadata
  - Google Books description is already in BookMetadata

Optional: Open Library ratings text is rarely available; we keep this module
for future public APIs and for normalizing review-like snippets.
"""

from __future__ import annotations

import re


_TONE_WORDS = re.compile(
    r"\b("
    r"wry|witty|funny|hilarious|comic|satirical|ironic|sarcastic|"
    r"melanchol\w*|bittersweet|tender|intimate|lyrical|spare|"
    r"bleak|dystopian|hopeful|devastating|heartbreaking|"
    r"playful|earnest|detached|clinical|dreamlike|surreal|"
    r"propulsive|slow-burn|page-turner|meditative|immersive|"
    r"sharp|elegant|raw|visceral|quiet|epic|sweeping|"
    r"coming-of-age|friendship|grief|loss|love|ambition"
    r")\b",
    re.IGNORECASE,
)


def extract_tone_snippets(*texts: str, limit: int = 12) -> list[str]:
    """
    Pull short sentences that carry tone/style language from public text.

    Helps the LLM hear *how readers and critics describe the book*, not only plot.
    """
    snippets: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        # Split on sentence-ish boundaries
        parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
        for sent in parts:
            s = re.sub(r"\s+", " ", sent).strip()
            if len(s) < 40 or len(s) > 280:
                continue
            if not _TONE_WORDS.search(s):
                continue
            key = s.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            snippets.append(s)
            if len(snippets) >= limit:
                return snippets
    return snippets
