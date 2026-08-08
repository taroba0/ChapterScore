"""Google Books API client — free tier works without a key (rate-limited)."""

from __future__ import annotations

import re
from typing import Any

import httpx

from chapterscore.books.http import create_client, get_json
from chapterscore.config import get_settings
from chapterscore.exceptions import BookFetchError
from chapterscore.models import BookMetadata

API_URL = "https://www.googleapis.com/books/v1/volumes"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def search_google_books(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> BookMetadata | None:
    own_client = client is None
    client = client or create_client()
    settings = get_settings()
    try:
        if isbn:
            q = f"isbn:{isbn.replace('-', '').strip()}"
        else:
            parts = [f"intitle:{title}"]
            if author:
                parts.append(f"inauthor:{author}")
            q = "+".join(parts)

        params: dict[str, Any] = {
            "q": q,
            "maxResults": 5,
            "printType": "books",
            "langRestrict": "en",
        }
        if settings.google_books_api_key:
            params["key"] = settings.google_books_api_key

        try:
            data = get_json(client, API_URL, params=params)
        except httpx.HTTPStatusError as exc:
            # Unauthenticated free tier is aggressively rate-limited (429).
            if exc.response is not None and exc.response.status_code in (429, 403):
                return None
            raise
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return None

        # Prefer the volume with the longest description
        best = max(
            items,
            key=lambda it: len((it.get("volumeInfo") or {}).get("description") or ""),
        )
        info = best.get("volumeInfo") or {}

        industry = info.get("industryIdentifiers") or []
        found_isbn = isbn
        for ident in industry:
            if ident.get("type") in ("ISBN_13", "ISBN_10"):
                found_isbn = ident.get("identifier")
                if ident.get("type") == "ISBN_13":
                    break

        image_links = info.get("imageLinks") or {}
        cover = image_links.get("large") or image_links.get("thumbnail") or image_links.get("smallThumbnail")
        if cover:
            cover = cover.replace("http://", "https://")

        description = _strip_html(info.get("description") or "")
        categories = list(info.get("categories") or [])

        return BookMetadata(
            title=info.get("title") or title,
            authors=list(info.get("authors") or []),
            isbn=found_isbn,
            description=description,
            subjects=categories,
            publish_year=(info.get("publishedDate") or "")[:4] or None,
            cover_url=cover,
            page_count=info.get("pageCount"),
            plot_summary=description,
            source="googlebooks",
            raw=best,
        )
    except httpx.HTTPError as exc:
        raise BookFetchError(
            f"Google Books request failed: {exc}",
            hint="You can set GOOGLE_BOOKS_API_KEY for higher rate limits.",
        ) from exc
    finally:
        if own_client:
            client.close()
