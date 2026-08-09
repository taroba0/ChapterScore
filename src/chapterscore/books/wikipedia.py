"""Wikipedia / MediaWiki API — plot summaries and chapter lists."""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from chapterscore.books.http import create_client, get_json
from chapterscore.exceptions import BookFetchError
from chapterscore.models import ChapterSummary

API_URL = "https://en.wikipedia.org/w/api.php"
REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


def _candidate_titles(book_title: str, author: str | None = None) -> list[str]:
    """Generate likely Wikipedia article titles for a novel."""
    title = book_title.strip()
    candidates = [
        title,
        f"{title} (novel)",
        f"{title} (book)",
    ]
    # Drop leading "The " variants sometimes help search more than direct title
    if author:
        last = author.strip().split()[-1]
        candidates.append(f"{title} ({last} novel)")
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def search_wikipedia_page(client: httpx.Client, book_title: str, author: str | None = None) -> str | None:
    """Return the best matching Wikipedia page title, or None."""
    # Try direct candidates first via REST summary (fast 404 on miss)
    for candidate in _candidate_titles(book_title, author):
        try:
            data = get_json(client, REST_SUMMARY.format(title=candidate.replace(" ", "_")))
            if isinstance(data, dict) and data.get("type") != "disambiguation" and data.get("title"):
                # Prefer pages that look like books
                extract = (data.get("extract") or "").lower()
                desc = (data.get("description") or "").lower()
                if any(
                    k in extract or k in desc
                    for k in ("novel", "book", "fiction", "author", "written", "published")
                ) or not desc:
                    return data["title"]
        except httpx.HTTPStatusError:
            continue
        except Exception:
            continue

    # Fallback: full-text search
    try:
        query = f"{book_title} novel"
        if author:
            query = f"{book_title} {author} novel"
        data = get_json(
            client,
            API_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 5,
                "format": "json",
            },
        )
        results = ((data.get("query") or {}).get("search") or []) if isinstance(data, dict) else []
        for hit in results:
            t = hit.get("title") or ""
            snippet = (hit.get("snippet") or "").lower()
            if "novel" in snippet or "book" in snippet or book_title.lower() in t.lower():
                return t
        if results:
            return results[0].get("title")
    except Exception:
        pass
    return None


def quick_chapter_list_available(
    book_title: str,
    author: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> bool:
    """
    Cheap check: does the Wikipedia article have a Chapters/Contents section?

    Only fetches the section *index* (not section bodies or full HTML).
    Suitable for UI Step 1 — not a substitute for real chapter extraction.
    """
    own_client = client is None
    client = client or create_client()
    try:
        page = search_wikipedia_page(client, book_title, author)
        if not page:
            return False
        data = get_json(
            client,
            API_URL,
            params={
                "action": "parse",
                "page": page,
                "prop": "sections",
                "format": "json",
            },
        )
        sections = ((data.get("parse") or {}).get("sections") or []) if isinstance(data, dict) else []
        for sec in sections:
            line = (sec.get("line") or "").lower()
            # "Chapters" / "Contents" / "Chapter list" — not plain "Plot"
            if re.search(r"\b(chapters?|contents|chapter\s+list|structure)\b", line):
                return True
        return False
    except Exception:
        return False
    finally:
        if own_client:
            client.close()


def fetch_wikipedia_plot(
    book_title: str,
    author: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> tuple[str, list[ChapterSummary], str | None]:
    """
    Return (plot_summary, chapters, page_title).

    Uses the MediaWiki API to pull Plot / Synopsis sections and, when present,
    a chapter list from the page HTML.
    """
    own_client = client is None
    client = client or create_client()
    try:
        page_title = search_wikipedia_page(client, book_title, author)
        if not page_title:
            return "", [], None

        plot = _extract_sections_by_pattern(
            client,
            page_title,
            r"^(plot|synopsis|summary|overview|story|premise|storyline)",
            max_chars=12000,
        )
        chapters = _extract_chapters_from_html(client, page_title)

        if not plot:
            try:
                summary = get_json(
                    client,
                    REST_SUMMARY.format(title=page_title.replace(" ", "_")),
                )
                if isinstance(summary, dict):
                    plot = summary.get("extract") or ""
            except Exception:
                pass

        return plot, chapters, page_title
    except httpx.HTTPError as exc:
        raise BookFetchError(f"Wikipedia request failed: {exc}") from exc
    finally:
        if own_client:
            client.close()


def fetch_wikipedia_enrichment(
    book_title: str,
    author: str | None = None,
    *,
    client: httpx.Client | None = None,
    page_title: str | None = None,
) -> dict[str, str]:
    """
    Pull Reception / Themes / Style / Background sections for literary texture.

    Returns dict with optional keys: reception, themes, background, lead.
    """
    own_client = client is None
    client = client or create_client()
    try:
        page = page_title or search_wikipedia_page(client, book_title, author)
        if not page:
            return {}
        out: dict[str, str] = {}
        reception = _extract_sections_by_pattern(
            client,
            page,
            r"^(reception|critical\s+reception|critical\s+response|reviews?|"
            r"literary\s+significance|awards|accolades|legacy)",
            max_chars=8000,
        )
        themes = _extract_sections_by_pattern(
            client,
            page,
            r"^(themes?|analysis|style|structure|characters?|setting|"
            r"writing\s+style|narrative|motifs?|symbols?|background|development|"
            r"publication|composition|influences?)",
            max_chars=8000,
        )
        if reception:
            out["reception"] = reception
        if themes:
            out["themes"] = themes
        try:
            summary = get_json(
                client,
                REST_SUMMARY.format(title=page.replace(" ", "_")),
            )
            if isinstance(summary, dict) and summary.get("extract"):
                out["lead"] = summary["extract"]
                if summary.get("description"):
                    out["wiki_description"] = str(summary["description"])
        except Exception:
            pass
        return out
    except Exception as exc:
        logger = __import__("logging").getLogger(__name__)
        logger.debug("Wikipedia enrichment failed: %s", exc)
        return {}
    finally:
        if own_client:
            client.close()


def _extract_sections_by_pattern(
    client: httpx.Client,
    page_title: str,
    pattern: str,
    *,
    max_chars: int = 12000,
) -> str:
    """Pull matching Wikipedia section plain text."""
    try:
        data = get_json(
            client,
            API_URL,
            params={
                "action": "parse",
                "page": page_title,
                "prop": "sections",
                "format": "json",
            },
        )
    except Exception:
        return ""

    sections = ((data.get("parse") or {}).get("sections") or []) if isinstance(data, dict) else []
    wanted_re = re.compile(pattern, re.IGNORECASE)
    texts: list[str] = []
    for sec in sections:
        line = sec.get("line") or ""
        if not wanted_re.search(line):
            continue
        index = sec.get("index")
        if index is None:
            continue
        try:
            sec_data = get_json(
                client,
                API_URL,
                params={
                    "action": "parse",
                    "page": page_title,
                    "prop": "text",
                    "section": index,
                    "format": "json",
                    "disableeditsection": 1,
                },
            )
            html = ((sec_data.get("parse") or {}).get("text") or {}).get("*") or ""
            soup = BeautifulSoup(html, "lxml")
            for tag in soup.select("sup.reference, table, .navbox, .hatnote, style, script"):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            if text and len(text) > 80:
                texts.append(f"## {line}\n{text}")
        except Exception:
            continue

    combined = "\n\n".join(texts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n…[truncated]"
    return combined


def _extract_chapters_from_html(client: httpx.Client, page_title: str) -> list[ChapterSummary]:
    """Best-effort chapter list from Wikipedia page content."""
    try:
        data = get_json(
            client,
            API_URL,
            params={
                "action": "parse",
                "page": page_title,
                "prop": "text",
                "format": "json",
                "disableeditsection": 1,
            },
        )
        html = ((data.get("parse") or {}).get("text") or {}).get("*") or ""
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    chapters: list[ChapterSummary] = []

    # Look for a "Chapters" or "Contents" section list
    for heading in soup.find_all(["h2", "h3"]):
        heading_text = heading.get_text(" ", strip=True).lower()
        if not any(k in heading_text for k in ("chapter", "contents", "structure")):
            continue
        # Next sibling lists
        for sib in heading.find_all_next(["ul", "ol"], limit=3):
            # Stop if we hit another heading of same/higher level
            items = sib.find_all("li", recursive=False)
            if len(items) < 3:
                continue
            for i, li in enumerate(items, start=1):
                raw = li.get_text(" ", strip=True)
                raw = re.sub(r"\s+", " ", raw)
                if len(raw) < 2 or len(raw) > 200:
                    continue
                # Parse "Chapter 1: Title" or just "Title"
                m = re.match(
                    r"^(?:chapter\s+)?(\d+|[IVXLCDM]+)[:.\s—–-]+(.+)$",
                    raw,
                    re.IGNORECASE,
                )
                if m:
                    chapters.append(ChapterSummary(number=m.group(1), title=m.group(2).strip()))
                else:
                    chapters.append(ChapterSummary(number=i, title=raw))
            if chapters:
                break
        if chapters:
            break

    # Also try definition-style chapter lists sometimes used in literary articles
    if not chapters:
        for li in soup.select(".mw-parser-output > ul > li")[:40]:
            raw = li.get_text(" ", strip=True)
            m = re.match(
                r"^(?:chapter\s+)?(\d+|[IVXLCDM]+)[:.\s—–-]+(.{3,120})$",
                raw,
                re.IGNORECASE,
            )
            if m:
                chapters.append(ChapterSummary(number=m.group(1), title=m.group(2).strip()))
        if len(chapters) < 3:
            chapters = []

    return chapters[:60]
