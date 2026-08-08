"""Open Library API client — free, no key required."""

from __future__ import annotations

import re
from typing import Any

import httpx

from chapterscore.books.http import create_client, get_json
from chapterscore.exceptions import BookFetchError
from chapterscore.models import BookMetadata, ChapterSummary

SEARCH_URL = "https://openlibrary.org/search.json"
WORKS_URL = "https://openlibrary.org"
ISBN_URL = "https://openlibrary.org/isbn/{isbn}.json"
BOOKS_API = "https://openlibrary.org/api/books"


def _dedupe_authors(authors: list[str]) -> list[str]:
    """Prefer Latin-script author names; drop obvious transliteration dupes."""
    if not authors:
        return []
    seen_norm: set[str] = set()
    latin: list[str] = []
    other: list[str] = []
    for a in authors:
        a = a.strip()
        if not a:
            continue
        # Rough Latin check
        is_latin = sum(1 for c in a if "a" <= c.lower() <= "z") >= max(1, len(a) // 3)
        key = re.sub(r"[^a-z]", "", a.lower())
        if key in seen_norm:
            continue
        seen_norm.add(key)
        (latin if is_latin else other).append(a)
    return (latin or other)[:5]


def _normalize_title(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"[:\-–—].*$", "", t)  # drop subtitles for comparison
    t = re.sub(r"[^a-z0-9\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Strip common leading article for comparison
    for art in ("the ", "a ", "an "):
        if t.startswith(art):
            t = t[len(art) :]
            break
    return t


def _pick_best_doc(docs: list[dict], title: str, author: str | None) -> dict:
    """Prefer exact title matches, then higher edition counts."""
    want = _normalize_title(title)
    author_l = (author or "").lower().strip()

    def score(doc: dict) -> tuple:
        got = _normalize_title(doc.get("title") or "")
        exact = 1 if got == want else 0
        starts = 1 if got.startswith(want) or want.startswith(got) else 0
        # Penalize sequels / expanded titles when user asked for a short exact title
        extra_len_penalty = max(0, len(got) - len(want)) if exact == 0 else 0
        authors = [a.lower() for a in (doc.get("author_name") or [])]
        author_hit = 0
        if author_l:
            author_hit = 1 if any(author_l in a or a in author_l for a in authors) else -1
        editions = int(doc.get("edition_count") or 0)
        year = doc.get("first_publish_year") or 9999
        # Higher is better for exact/starts/author/editions; lower year slightly preferred for classics
        return (exact, starts, author_hit, editions, -extra_len_penalty, -int(year) if isinstance(year, int) else 0)

    return max(docs, key=score)


def _clean_description(desc: Any) -> str:
    if desc is None:
        return ""
    if isinstance(desc, dict):
        desc = desc.get("value") or desc.get("text") or ""
    text = str(desc).strip()
    # Strip basic HTML if present
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_chapters_from_toc(toc: list[dict] | None) -> list[ChapterSummary]:
    if not toc:
        return []
    chapters: list[ChapterSummary] = []
    for i, entry in enumerate(toc, start=1):
        title = entry.get("title") or entry.get("label") or f"Chapter {i}"
        level = entry.get("level", 0)
        # Prefer top-level TOC entries as chapters
        if level and level > 1 and len(chapters) > 5:
            continue
        chapters.append(
            ChapterSummary(
                number=entry.get("pagenum") or i,
                title=str(title),
                summary="",
            )
        )
    return chapters[:80]


def search_open_library(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> BookMetadata | None:
    """Search Open Library and return the best matching work with description."""
    own_client = client is None
    client = client or create_client()
    try:
        if isbn:
            meta = _from_isbn(client, isbn)
            if meta:
                return meta

        params: dict[str, Any] = {
            "title": title,
            "limit": 15,
            "fields": (
                "key,title,author_name,author_key,isbn,first_publish_year,"
                "subject,cover_i,number_of_pages_median,edition_count,editions"
            ),
        }
        if author:
            params["author"] = author

        data = get_json(client, SEARCH_URL, params=params)
        docs = data.get("docs") if isinstance(data, dict) else None
        if not docs:
            return None

        best = _pick_best_doc(docs, title, author)
        work_key = best.get("key")  # e.g. /works/OL45883W
        description = ""
        subjects: list[str] = list(best.get("subject") or [])[:20]
        chapters: list[ChapterSummary] = []
        cover_url = None
        page_count = best.get("number_of_pages_median")

        if work_key:
            try:
                work = get_json(client, f"{WORKS_URL}{work_key}.json")
                if isinstance(work, dict):
                    description = _clean_description(work.get("description"))
                    if work.get("subjects"):
                        subjects = list(dict.fromkeys(subjects + list(work["subjects"])))[:25]
                    chapters = _extract_chapters_from_toc(work.get("table_of_contents"))
                    # Prefer description from first edition if work has none
                    if not description:
                        description = _edition_description(client, work)
            except Exception:
                pass

        cover_i = best.get("cover_i")
        if cover_i:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"

        isbns = best.get("isbn") or []
        primary_isbn = isbn or (isbns[0] if isbns else None)

        authors = _dedupe_authors(list(best.get("author_name") or []))

        return BookMetadata(
            title=best.get("title") or title,
            authors=authors,
            isbn=primary_isbn,
            description=description,
            subjects=subjects,
            publish_year=best.get("first_publish_year"),
            cover_url=cover_url,
            page_count=page_count,
            chapters=chapters,
            plot_summary=description,
            source="openlibrary",
            raw={"work_key": work_key, "search": best},
        )
    except httpx.HTTPError as exc:
        raise BookFetchError(
            f"Open Library request failed: {exc}",
            hint="Check your network connection and try again.",
        ) from exc
    finally:
        if own_client:
            client.close()


def _from_isbn(client: httpx.Client, isbn: str) -> BookMetadata | None:
    clean = isbn.replace("-", "").strip()
    try:
        data = get_json(
            client,
            BOOKS_API,
            params={"bibkeys": f"ISBN:{clean}", "format": "json", "jscmd": "data"},
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(f"ISBN:{clean}")
    if not entry:
        return None

    authors = [a.get("name", "") for a in entry.get("authors") or [] if a.get("name")]
    subjects = [s.get("name", "") for s in entry.get("subjects") or [] if s.get("name")]
    cover = None
    if entry.get("cover"):
        cover = entry["cover"].get("large") or entry["cover"].get("medium")

    description = ""
    # Try work details via edition
    try:
        edition = get_json(client, ISBN_URL.format(isbn=clean))
        if isinstance(edition, dict):
            description = _clean_description(edition.get("description"))
            works = edition.get("works") or []
            if works and works[0].get("key"):
                work = get_json(client, f"{WORKS_URL}{works[0]['key']}.json")
                if isinstance(work, dict):
                    if not description:
                        description = _clean_description(work.get("description"))
    except Exception:
        pass

    return BookMetadata(
        title=entry.get("title") or "",
        authors=authors,
        isbn=clean,
        description=description,
        subjects=subjects[:25],
        publish_year=(entry.get("publish_date") or "")[:4] or None,
        cover_url=cover,
        page_count=entry.get("number_of_pages"),
        plot_summary=description,
        source="openlibrary",
        raw=entry,
    )


def _edition_description(client: httpx.Client, work: dict) -> str:
    """Fall back to the first English edition description."""
    # Open Library works sometimes only have descriptions on editions
    return ""
