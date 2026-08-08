"""Aggregate book metadata from Open Library, Google Books, and Wikipedia."""

from __future__ import annotations

import logging

import httpx

from chapterscore.books.googlebooks import search_google_books
from chapterscore.books.http import create_client
from chapterscore.books.openlibrary import search_open_library
from chapterscore.books.wikipedia import fetch_wikipedia_plot
from chapterscore.cache import Cache, book_cache_key
from chapterscore.exceptions import BookNotFoundError
from chapterscore.models import BookMetadata, ChapterSummary

logger = logging.getLogger(__name__)


def _longer(a: str, b: str) -> str:
    return a if len(a or "") >= len(b or "") else b


def _merge_metadata(primary: BookMetadata, secondary: BookMetadata | None) -> BookMetadata:
    if secondary is None:
        return primary
    return BookMetadata(
        title=primary.title or secondary.title,
        authors=primary.authors or secondary.authors,
        isbn=primary.isbn or secondary.isbn,
        description=_longer(primary.description, secondary.description),
        subjects=list(dict.fromkeys(primary.subjects + secondary.subjects))[:30],
        publish_year=primary.publish_year or secondary.publish_year,
        cover_url=primary.cover_url or secondary.cover_url,
        page_count=primary.page_count or secondary.page_count,
        chapters=primary.chapters or secondary.chapters,
        plot_summary=_longer(primary.plot_summary, secondary.plot_summary),
        source="+".join(filter(None, [primary.source, secondary.source])),
        raw={"primary": primary.source, "secondary": secondary.source},
    )


def _synthetic_chapters(n: int = 12) -> list[ChapterSummary]:
    """When no real chapter list exists, create numbered placeholders for chapter mode."""
    return [
        ChapterSummary(
            number=i,
            title=f"Chapter {i}",
            summary="",
        )
        for i in range(1, n + 1)
    ]


def fetch_book(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    *,
    use_cache: bool = True,
    want_chapters: bool = False,
) -> BookMetadata:
    """
    Resolve a book from public sources and enrich with plot/chapter data.

    Strategy:
      1. Open Library (structured metadata)
      2. Google Books (often better descriptions)
      3. Wikipedia (plot + chapter lists)
      4. Merge into a single BookMetadata
    """
    title = title.strip()
    if not title and not isbn:
        raise BookNotFoundError("Provide a book title or ISBN.")

    cache = Cache()
    cache_key = book_cache_key(title or "", author, isbn)
    if use_cache:
        cached = cache.get_model("book", cache_key, BookMetadata)
        if cached is not None:
            # If chapter mode needs chapters and cache has none, refresh enrichment
            if not (want_chapters and not cached.chapters):
                logger.debug("Book cache hit: %s", cache_key)
                return cached

    with create_client() as client:
        ol: BookMetadata | None = None
        gb: BookMetadata | None = None

        try:
            ol = search_open_library(title, author, isbn, client=client)
        except Exception as exc:
            logger.debug("Open Library failed: %s", exc)

        try:
            gb = search_google_books(
                title or (ol.title if ol else ""), author, isbn, client=client
            )
        except Exception as exc:
            logger.debug("Google Books failed: %s", exc)

        if ol is None and gb is None:
            # Last resort: still try Wikipedia with the raw title
            plot, chapters, page = fetch_wikipedia_plot(title, author, client=client)
            if not plot and not page:
                raise BookNotFoundError(
                    f"Could not find book metadata for “{title}”.",
                    hint="Try a more exact title, add --author, or pass --isbn.",
                )
            book = BookMetadata(
                title=title,
                authors=[author] if author else [],
                isbn=isbn,
                description=plot[:2000],
                plot_summary=plot,
                chapters=chapters,
                source="wikipedia",
            )
        else:
            # Prefer the source with the richer description as primary
            if ol and gb:
                primary, secondary = (ol, gb) if len(ol.description) >= len(gb.description) else (gb, ol)
                book = _merge_metadata(primary, secondary)
            else:
                book = ol or gb  # type: ignore[assignment]

            # Wikipedia enrichment
            try:
                plot, chapters, page = fetch_wikipedia_plot(
                    book.title,
                    book.authors[0] if book.authors else author,
                    client=client,
                )
                if plot:
                    book.plot_summary = _longer(book.plot_summary, plot)
                    if len(plot) > len(book.description):
                        # Keep a shorter blurb in description, full plot separately
                        if not book.description:
                            book.description = plot[:2000]
                if chapters and (not book.chapters or len(chapters) > len(book.chapters)):
                    book.chapters = chapters
                if page:
                    book.raw = {**(book.raw or {}), "wikipedia_page": page}
                book.source = "+".join(filter(None, [book.source, "wikipedia" if page else ""]))
            except Exception as exc:
                logger.debug("Wikipedia enrichment failed: %s", exc)

    # Ensure we have *some* text for analysis
    if not book.plot_summary and book.description:
        book.plot_summary = book.description
    if not book.description and book.plot_summary:
        book.description = book.plot_summary[:2000]

    if not book.plot_summary and not book.description:
        # Still usable — Grok can work from title/author/subjects alone, with caveats
        book.plot_summary = (
            f"No public plot summary found. Title: {book.title}. "
            f"Author(s): {book.author_str}. "
            f"Subjects: {', '.join(book.subjects[:10]) or 'unknown'}."
        )

    if want_chapters and not book.chapters:
        # Estimate chapter count from page count, or default to 12 arcs
        n = 12
        if book.page_count:
            n = max(8, min(30, round(book.page_count / 25)))
        book.chapters = _synthetic_chapters(n)
        book.raw = {**(book.raw or {}), "synthetic_chapters": True}

    if use_cache:
        cache.set("book", cache_key, book)

    return book
