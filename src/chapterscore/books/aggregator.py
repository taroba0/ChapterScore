"""Aggregate book metadata from Open Library, Google Books, and Wikipedia."""

from __future__ import annotations

import logging

from chapterscore.books.googlebooks import search_google_books
from chapterscore.books.http import create_client
from chapterscore.books.openlibrary import search_open_library
from chapterscore.books.reviews import extract_tone_snippets
from chapterscore.books.wikipedia import fetch_wikipedia_enrichment, fetch_wikipedia_plot
from chapterscore.cache import Cache, book_cache_key
from chapterscore.exceptions import BookNotFoundError
from chapterscore.models import BookMetadata, ChapterSummary

logger = logging.getLogger(__name__)

# Bump when enrichment shape changes so old thin caches refresh
_BOOK_CACHE_VERSION = "v2"


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
        subjects=list(dict.fromkeys(primary.subjects + secondary.subjects))[:40],
        publish_year=primary.publish_year or secondary.publish_year,
        cover_url=primary.cover_url or secondary.cover_url,
        page_count=primary.page_count or secondary.page_count,
        chapters=primary.chapters or secondary.chapters,
        plot_summary=_longer(primary.plot_summary, secondary.plot_summary),
        publisher_blurb=_longer(primary.publisher_blurb, secondary.publisher_blurb)
        or _longer(primary.description, secondary.description),
        reception_text=_longer(primary.reception_text, secondary.reception_text),
        themes_text=_longer(primary.themes_text, secondary.themes_text),
        review_snippets=list(
            dict.fromkeys((primary.review_snippets or []) + (secondary.review_snippets or []))
        )[:12],
        genre_labels=list(
            dict.fromkeys((primary.genre_labels or []) + (secondary.genre_labels or []))
        )[:20],
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


def _infer_genre_labels(book: BookMetadata) -> list[str]:
    labels: list[str] = []
    blob = " ".join(book.subjects[:30]).lower()
    mapping = [
        ("science fiction", ("science fiction", "sci-fi", "scifi", "dystopia", "space opera")),
        ("fantasy", ("fantasy", "epic fantasy", "magical")),
        ("literary fiction", ("literary", "fiction, general")),
        ("romance", ("romance", "love stories")),
        ("mystery", ("mystery", "detective", "crime")),
        ("historical", ("historical fiction", "history")),
        ("young adult", ("young adult", "juvenile", "ya ")),
        ("horror", ("horror", "gothic")),
        ("thriller", ("thriller", "suspense")),
    ]
    for label, keys in mapping:
        if any(k in blob for k in keys):
            labels.append(label)
    return labels


# Stage 1 discovery lives in discovery.py (multi-strategy, multi-candidate).
# Re-export for stable imports.
from chapterscore.books.discovery import (  # noqa: E402
    lookup_book_quick,
    search_book_candidates,
)


def fetch_book(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    *,
    use_cache: bool = True,
    want_chapters: bool = False,
) -> BookMetadata:
    """
    Resolve a book from public sources and enrich with plot + literary signals.

    Strategy:
      1. Open Library (structured metadata)
      2. Google Books (publisher blurbs)
      3. Wikipedia (plot, reception, themes, chapters)
      4. Tone snippets from public review/reception language
      5. Merge into a single BookMetadata
    """
    title = title.strip()
    if not title and not isbn:
        raise BookNotFoundError("Provide a book title or ISBN.")

    cache = Cache()
    cache_key = book_cache_key(title or "", author, isbn) + f"|{_BOOK_CACHE_VERSION}"
    if use_cache:
        cached = cache.get_model("book", cache_key, BookMetadata)
        if cached is not None:
            # Refresh if chapter mode needs chapters or enrichment is thin
            needs_refresh = want_chapters and not cached.chapters
            thin = not (cached.plot_summary or cached.reception_text or cached.themes_text)
            if not needs_refresh and not thin:
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
            wiki_page = page
        else:
            if ol and gb:
                primary, secondary = (
                    (ol, gb) if len(ol.description) >= len(gb.description) else (gb, ol)
                )
                book = _merge_metadata(primary, secondary)
            else:
                book = ol or gb  # type: ignore[assignment]

            # Prefer Google blurb as publisher_blurb when present
            if gb and gb.description:
                book.publisher_blurb = book.publisher_blurb or gb.description
            if ol and ol.description and not book.publisher_blurb:
                book.publisher_blurb = ol.description

            wiki_page = None
            try:
                plot, chapters, page = fetch_wikipedia_plot(
                    book.title,
                    book.authors[0] if book.authors else author,
                    client=client,
                )
                wiki_page = page
                if plot:
                    book.plot_summary = _longer(book.plot_summary, plot)
                    if not book.description:
                        book.description = plot[:2000]
                if chapters and (not book.chapters or len(chapters) > len(book.chapters)):
                    book.chapters = chapters
                if page:
                    book.raw = {**(book.raw or {}), "wikipedia_page": page}
                book.source = "+".join(
                    filter(None, [book.source, "wikipedia" if page else ""])
                )
            except Exception as exc:
                logger.debug("Wikipedia plot enrichment failed: %s", exc)

        # Literary enrichment: reception + themes (public Wikipedia sections)
        try:
            enrich = fetch_wikipedia_enrichment(
                book.title,
                book.authors[0] if book.authors else author,
                client=client,
                page_title=wiki_page or (book.raw or {}).get("wikipedia_page"),
            )
            if enrich.get("reception"):
                book.reception_text = _longer(book.reception_text, enrich["reception"])
            if enrich.get("themes"):
                book.themes_text = _longer(book.themes_text, enrich["themes"])
            if enrich.get("lead") and not book.description:
                book.description = enrich["lead"][:2000]
            if enrich.get("wiki_description"):
                book.genre_labels = list(
                    dict.fromkeys(
                        book.genre_labels + [enrich["wiki_description"]]
                    )
                )[:20]
            if enrich:
                book.source = "+".join(
                    filter(None, [book.source, "wikipedia-enrich"])
                )
        except Exception as exc:
            logger.debug("Wikipedia literary enrichment failed: %s", exc)

    # Tone language from public reception/description (not full reviews scrape)
    snippets = extract_tone_snippets(
        book.reception_text,
        book.themes_text,
        book.publisher_blurb,
        book.description,
        book.plot_summary[:3000] if book.plot_summary else "",
    )
    if snippets:
        book.review_snippets = list(dict.fromkeys(book.review_snippets + snippets))[:12]

    book.genre_labels = list(
        dict.fromkeys(book.genre_labels + _infer_genre_labels(book))
    )[:20]

    # Ensure we have *some* text for analysis
    if not book.plot_summary and book.description:
        book.plot_summary = book.description
    if not book.description and book.plot_summary:
        book.description = book.plot_summary[:2000]
    if not book.publisher_blurb:
        book.publisher_blurb = book.description

    if not book.plot_summary and not book.description:
        book.plot_summary = (
            f"No public plot summary found. Title: {book.title}. "
            f"Author(s): {book.author_str}. "
            f"Subjects: {', '.join(book.subjects[:10]) or 'unknown'}."
        )

    if want_chapters and not book.chapters:
        n = 12
        if book.page_count:
            n = max(8, min(30, round(book.page_count / 25)))
        book.chapters = _synthetic_chapters(n)
        book.raw = {**(book.raw or {}), "synthetic_chapters": True}

    if use_cache:
        cache.set("book", cache_key, book)

    return book
