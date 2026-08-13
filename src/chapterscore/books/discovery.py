"""Stage 1 multi-strategy book discovery (fast identity only).

Searches Open Library + Google Books with several query variants, ranks
candidates, and returns a shortlist for user confirmation. No Grok, no
Wikipedia plot/reception enrichment — that stays in Stage 2 ``fetch_book``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from chapterscore.books.http import create_client, get_json
from chapterscore.cache import Cache, book_cache_key
from chapterscore.config import get_settings
from chapterscore.exceptions import BookNotFoundError
from chapterscore.models import BookMetadata

logger = logging.getLogger(__name__)

_OL_SEARCH = "https://openlibrary.org/search.json"
_GB_API = "https://www.googleapis.com/books/v1/volumes"
_CACHE_NS = "book_candidates"
_CACHE_VER = "cand_v1"


def normalize_title_key(title: str) -> str:
    """Normalize for comparison / dedupe."""
    t = (title or "").lower().strip()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[:\-–—].*$", "", t)  # drop subtitle after colon/dash
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for art in ("the ", "a ", "an "):
        if t.startswith(art):
            t = t[len(art) :]
            break
    return t


def title_query_variants(title: str) -> list[str]:
    """
    Produce lightweight title variants for multi-strategy search.

    Order: original → punctuation-normalized → without subtitle → core tokens.
    """
    raw = (title or "").strip()
    if not raw:
        return []
    variants: list[str] = []

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", (s or "").strip())
        if s and s.lower() not in {v.lower() for v in variants}:
            variants.append(s)

    add(raw)
    # Unicode quotes / dashes → ASCII
    simplified = (
        raw.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
    )
    add(simplified)
    # Drop trailing parenthetical / bracket series marks
    add(re.sub(r"\s*[\(\[\{].*?[\)\]\}]\s*$", "", simplified).strip())
    # Without subtitle after colon or em/en dash
    add(re.sub(r"\s*[:\-–—].*$", "", simplified).strip())
    # Alphanumeric + spaces only (ASCII fold-ish: keep letters/digits/space)
    alnum = re.sub(r"[^\w\s]", " ", simplified, flags=re.UNICODE)
    alnum = re.sub(r"\s+", " ", alnum).strip()
    add(alnum)
    words_all = alnum.split()
    # Significant words (drop tiny stopwords)
    words = [w for w in words_all if w.lower() not in {"a", "an", "the", "of", "and"}]
    if len(words) >= 3:
        add(" ".join(words[:6]))
    # Drop leading single-letter / "I" for partial match (helps long first-person titles)
    if len(words_all) >= 4:
        add(" ".join(words_all[1:]))
    if len(words) >= 4:
        add(" ".join(words[1:6]))
    # Last 4 content words (distinctive tail)
    if len(words) >= 5:
        add(" ".join(words[-4:]))
    return variants[:8]


def _token_set(s: str) -> set[str]:
    return {t for t in normalize_title_key(s).split() if len(t) > 1}


def _title_similarity(query: str, candidate: str) -> float:
    """0–1 rough title similarity."""
    qn = normalize_title_key(query)
    cn = normalize_title_key(candidate)
    if not qn or not cn:
        return 0.0
    if qn == cn:
        return 1.0
    if qn in cn or cn in qn:
        return 0.88
    qt, ct = _token_set(query), _token_set(candidate)
    if not qt or not ct:
        return 0.0
    overlap = len(qt & ct) / max(len(qt), len(ct))
    # Bonus if most query tokens appear
    coverage = len(qt & ct) / len(qt)
    return min(1.0, 0.55 * overlap + 0.45 * coverage)


def _author_match(query_author: str | None, authors: list[str]) -> float:
    if not query_author:
        return 0.0
    qa = re.sub(r"[^a-z\s]", "", query_author.lower()).strip()
    if not qa:
        return 0.0
    for a in authors:
        aa = re.sub(r"[^a-z\s]", "", (a or "").lower()).strip()
        if not aa:
            continue
        if qa == aa or qa in aa or aa in qa:
            return 1.0
        q_parts = set(qa.split())
        a_parts = set(aa.split())
        if q_parts & a_parts and len(q_parts & a_parts) >= 1:
            # last-name style hit
            if any(len(p) >= 4 and p in a_parts for p in q_parts):
                return 0.85
            return 0.5
    return -0.4  # author provided but no match — soft penalty


def _looks_latin_script(title: str) -> bool:
    letters = [c for c in (title or "") if c.isalpha()]
    if not letters:
        return True
    latin = sum(1 for c in letters if ("a" <= c.lower() <= "z") or c in "àáâãäåèéêëìíîïòóôõöùúûüýÿçñ")
    # Prefer mostly basic Latin A–Z for English-query ranking
    basic = sum(1 for c in letters if "a" <= c.lower() <= "z")
    return basic / len(letters) >= 0.75


def score_candidate(
    book: BookMetadata,
    *,
    query_title: str,
    query_author: str | None,
) -> float:
    """Higher is better. Used to order Stage 1 candidates."""
    sim = _title_similarity(query_title, book.title)
    auth = _author_match(query_author, book.authors)
    score = 100.0 * sim
    score += 25.0 * auth if auth > 0 else (8.0 * auth)  # penalty if negative
    if book.page_count:
        score += 4.0
    if book.publish_year:
        score += 2.0
    if book.isbn:
        score += 3.0
    if book.description or book.publisher_blurb:
        score += 2.0
    # Prefer English-ish / fiction-looking subjects lightly
    subj = " ".join(book.subjects[:8]).lower()
    if any(k in subj for k in ("fiction", "novel", "literature")):
        score += 3.0
    # When the user typed a Latin/English title, demote foreign-language originals
    # that only matched via author (e.g. French "Moi qui…" for Harpman).
    q_latin = _looks_latin_script(query_title)
    c_latin = _looks_latin_script(book.title)
    if q_latin and not c_latin and sim < 0.45:
        score -= 40.0
    if q_latin and c_latin and sim >= 0.55:
        score += 8.0
    return score


def _candidate_key(book: BookMetadata) -> str:
    if book.isbn:
        return f"isbn:{book.isbn.replace('-', '').strip()}"
    authors = ",".join(sorted(normalize_title_key(a) for a in (book.authors or [])[:2]))
    return f"t:{normalize_title_key(book.title)}|a:{authors}"


def _merge_candidate(a: BookMetadata, b: BookMetadata) -> BookMetadata:
    """Prefer richer fields when two sources hit the same work."""
    return BookMetadata(
        title=a.title if len(a.title or "") >= len(b.title or "") else b.title,
        authors=a.authors or b.authors,
        isbn=a.isbn or b.isbn,
        description=a.description if len(a.description or "") >= len(b.description or "") else b.description,
        subjects=list(dict.fromkeys((a.subjects or []) + (b.subjects or [])))[:30],
        publish_year=a.publish_year or b.publish_year,
        cover_url=a.cover_url or b.cover_url,
        page_count=a.page_count or b.page_count,
        publisher_blurb=a.publisher_blurb or b.publisher_blurb or a.description or b.description,
        plot_summary=a.plot_summary or b.plot_summary,
        genre_labels=list(dict.fromkeys((a.genre_labels or []) + (b.genre_labels or [])))[:15],
        source="+".join(filter(None, [a.source, b.source])),
        raw={**(a.raw or {}), **(b.raw or {}), "merged": True},
    )


def _dedupe_and_rank(
    candidates: list[BookMetadata],
    *,
    query_title: str,
    query_author: str | None,
    limit: int,
) -> list[BookMetadata]:
    merged: dict[str, BookMetadata] = {}
    for c in candidates:
        if not (c.title or "").strip():
            continue
        key = _candidate_key(c)
        if key in merged:
            merged[key] = _merge_candidate(merged[key], c)
        else:
            merged[key] = c
    ranked = sorted(
        merged.values(),
        key=lambda b: score_candidate(b, query_title=query_title, query_author=query_author),
        reverse=True,
    )
    # Drop very weak title matches unless nothing better exists
    strong = [
        b
        for b in ranked
        if _title_similarity(query_title, b.title) >= 0.35
        or (query_author and _author_match(query_author, b.authors) >= 0.85)
    ]
    pool = strong if strong else ranked
    # If we already have a clear title hit, hide unrelated other books by same author
    if pool and _title_similarity(query_title, pool[0].title) >= 0.55:
        focused = [
            b
            for b in pool
            if _title_similarity(query_title, b.title) >= 0.28
        ]
        if focused:
            pool = focused
    return pool[:limit]


# ── Open Library multi-strategy ──────────────────────────────────────────────


def _ol_doc_to_book(doc: dict[str, Any], *, source_tag: str = "openlibrary") -> BookMetadata:
    authors = list(doc.get("author_name") or [])[:5]
    isbns = doc.get("isbn") or []
    cover_i = doc.get("cover_i")
    cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None
    return BookMetadata(
        title=doc.get("title") or "",
        authors=authors,
        isbn=isbns[0] if isbns else None,
        subjects=list(doc.get("subject") or [])[:15],
        publish_year=doc.get("first_publish_year"),
        cover_url=cover_url,
        page_count=doc.get("number_of_pages_median"),
        source=source_tag,
        raw={"work_key": doc.get("key"), "ol_doc": doc, "strategy": source_tag},
    )


def _ol_search_once(
    client: httpx.Client,
    params: dict[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    p = {
        **params,
        "limit": limit,
        "fields": (
            "key,title,author_name,isbn,first_publish_year,subject,"
            "cover_i,number_of_pages_median,edition_count"
        ),
    }
    try:
        data = get_json(client, _OL_SEARCH, params=p)
    except Exception as exc:
        logger.debug("OL search failed %s: %s", params, exc)
        return []
    docs = data.get("docs") if isinstance(data, dict) else None
    return list(docs or [])


def _ol_enrich_from_editions(
    client: httpx.Client,
    book: BookMetadata,
    *,
    query_title: str,
) -> BookMetadata:
    """
    Prefer an edition title that matches the user's query language.

    Open Library often stores the *work* under the original-language title
    (e.g. French) while English translations live only on editions — critical
    for titles like “I Who Have Never Known Men”.
    """
    work_key = (book.raw or {}).get("work_key")
    if not work_key or not str(work_key).startswith("/works/"):
        return book
    try:
        data = get_json(
            client,
            f"https://openlibrary.org{work_key}/editions.json",
            params={"limit": 25},
        )
    except Exception as exc:
        logger.debug("OL editions fetch failed for %s: %s", work_key, exc)
        return book

    entries = (data or {}).get("entries") if isinstance(data, dict) else None
    if not entries:
        return book

    best_ed: dict[str, Any] | None = None
    best_score = -1.0
    for ed in entries:
        et = (ed.get("title") or "").strip()
        if not et:
            continue
        s = _title_similarity(query_title, et)
        # Prefer English-script edition titles slightly when query is Latin
        if _looks_latin_script(query_title) and _looks_latin_script(et):
            s += 0.05
        langs = ed.get("languages") or []
        lang_keys = " ".join(
            str((lng or {}).get("key") or lng) for lng in langs
        ).lower()
        if "eng" in lang_keys or "/languages/en" in lang_keys:
            s += 0.08
        if s > best_score:
            best_score = s
            best_ed = ed

    if not best_ed or best_score < 0.35:
        return book

    # Apply edition fields onto the candidate for display / Stage 2 identity
    book.title = (best_ed.get("title") or book.title).strip()
    pages = best_ed.get("number_of_pages")
    if pages and not book.page_count:
        book.page_count = pages
    pub = best_ed.get("publish_date") or ""
    year = None
    m = re.search(r"(19|20)\d{2}", str(pub))
    if m:
        year = int(m.group(0))
    if year and not book.publish_year:
        book.publish_year = year
    # ISBN from edition
    for key in ("isbn_13", "isbn_10"):
        vals = best_ed.get(key) or []
        if vals:
            book.isbn = book.isbn or vals[0]
            break
    book.raw = {
        **(book.raw or {}),
        "edition_title": best_ed.get("title"),
        "edition_key": best_ed.get("key"),
        "edition_match_score": round(best_score, 3),
    }
    return book


def search_open_library_candidates(
    title: str,
    author: str | None = None,
    *,
    client: httpx.Client,
    per_query: int = 10,
    enrich_editions: bool = True,
) -> list[BookMetadata]:
    """Multi-strategy Open Library search → lightweight BookMetadata list."""
    seen_keys: set[str] = set()
    out: list[BookMetadata] = []

    def absorb(docs: list[dict[str, Any]], tag: str) -> None:
        for doc in docs:
            key = doc.get("key") or f"{doc.get('title')}|{doc.get('author_name')}"
            if key in seen_keys:
                continue
            seen_keys.add(str(key))
            book = _ol_doc_to_book(doc, source_tag=f"openlibrary:{tag}")
            if book.title:
                out.append(book)

    variants = title_query_variants(title)
    strategies: list[tuple[str, dict[str, Any]]] = []

    for i, v in enumerate(variants):
        # Fielded title search
        p_title: dict[str, Any] = {"title": v}
        if author and i == 0:
            p_title["author"] = author
        strategies.append((f"title[{i}]", p_title))
        # Free-text q (often finds titles that fielded search misses)
        q = v if not author else f"{v} {author}"
        strategies.append((f"q[{i}]", {"q": q}))
        # Prefer English editions when available (Open Library language filter)
        if i < 2:
            strategies.append((f"q-eng[{i}]", {"q": q, "language": "eng"}))

    if author:
        strategies.append(("author+title", {"author": author, "title": variants[0]}))
        strategies.append(("q-author-first", {"q": f"{author} {variants[0]}"}))
        strategies.append(
            ("q-author-eng", {"q": f"{author} {variants[0]}", "language": "eng"})
        )
        # Author-only: catch works stored under original-language titles
        strategies.append(("author-only", {"author": author}))

    # Cap strategies for speed (first variants matter most)
    for tag, params in strategies[:14]:
        absorb(_ol_search_once(client, params, limit=per_query), tag)
        if len(out) >= 30:
            break

    # Edition-title enrichment (fixes translated works indexed under original title)
    if enrich_editions and out:
        # Unique works only; cap network cost
        by_work: dict[str, BookMetadata] = {}
        passthrough: list[BookMetadata] = []
        for b in out:
            wk = str((b.raw or {}).get("work_key") or "")
            if wk.startswith("/works/"):
                if wk not in by_work:
                    by_work[wk] = b
            else:
                passthrough.append(b)
        enriched: list[BookMetadata] = []
        for i, (wk, b) in enumerate(by_work.items()):
            if i >= 10:
                enriched.append(b)
                continue
            enriched.append(
                _ol_enrich_from_editions(client, b, query_title=title)
            )
        out = enriched + passthrough

    return out


# ── Google Books multi-strategy ──────────────────────────────────────────────


def _gb_item_to_book(item: dict[str, Any], *, tag: str) -> BookMetadata | None:
    info = item.get("volumeInfo") or {}
    title = info.get("title") or ""
    if not title:
        return None
    industry = info.get("industryIdentifiers") or []
    found_isbn = None
    for ident in industry:
        if ident.get("type") in ("ISBN_13", "ISBN_10"):
            found_isbn = ident.get("identifier")
            if ident.get("type") == "ISBN_13":
                break
    image_links = info.get("imageLinks") or {}
    cover = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if cover:
        cover = cover.replace("http://", "https://")
    desc = info.get("description") or ""
    desc = re.sub(r"<[^>]+>", " ", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    return BookMetadata(
        title=title,
        authors=list(info.get("authors") or []),
        isbn=found_isbn,
        description=desc,
        subjects=list(info.get("categories") or []),
        publish_year=(info.get("publishedDate") or "")[:4] or None,
        cover_url=cover,
        page_count=info.get("pageCount"),
        publisher_blurb=desc,
        plot_summary=desc,
        source=f"googlebooks:{tag}",
        raw={"gb_id": item.get("id"), "strategy": tag},
    )


def _gb_search_once(
    client: httpx.Client,
    q: str,
    *,
    max_results: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Returns (items, rate_limited).
    rate_limited=True means caller should stop further Google Books queries.
    """
    settings = get_settings()
    params: dict[str, Any] = {
        "q": q,
        "maxResults": min(max_results, 20),
        "printType": "books",
    }
    # Prefer English when no API key (still works without key when not rate-limited)
    params["langRestrict"] = "en"
    if settings.google_books_api_key:
        params["key"] = settings.google_books_api_key
    try:
        data = get_json(client, _GB_API, params=params)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code in (429, 403):
            logger.debug("Google Books rate-limited on %r", q)
            return [], True
        logger.debug("Google Books HTTP error on %r: %s", q, exc)
        return [], False
    except Exception as exc:
        logger.debug("Google Books failed on %r: %s", q, exc)
        return [], False
    items = data.get("items") if isinstance(data, dict) else None
    return list(items or []), False


def search_google_books_candidates(
    title: str,
    author: str | None = None,
    *,
    client: httpx.Client,
    per_query: int = 8,
) -> list[BookMetadata]:
    """Multi-strategy Google Books search → lightweight BookMetadata list."""
    seen_ids: set[str] = set()
    out: list[BookMetadata] = []
    variants = title_query_variants(title)

    # Fewer, higher-value queries — free tier is aggressively rate-limited
    queries: list[tuple[str, str]] = []
    primary = variants[0]
    queries.append(("quoted", f'"{primary}"'))
    quoted_title = f'intitle:"{primary}"' if " " in primary else f"intitle:{primary}"
    if author:
        queries.append(("intitle+author", f'{quoted_title}+inauthor:"{author}"'))
        queries.append(("free+author", f"{primary} {author}"))
        queries.append(("author-quoted", f'inauthor:"{author}" "{primary}"'))
    else:
        queries.append(("intitle", quoted_title))
        queries.append(("free", primary))
    # Partial variants (2nd / 3rd) if distinct
    for i, v in enumerate(variants[1:4], start=1):
        queries.append((f"free[{i}]", f"{v} {author}" if author else v))

    for tag, q in queries[:8]:
        items, rate_limited = _gb_search_once(client, q, max_results=per_query)
        for item in items:
            iid = item.get("id") or ""
            if iid and iid in seen_ids:
                continue
            if iid:
                seen_ids.add(iid)
            book = _gb_item_to_book(item, tag=tag)
            if book:
                out.append(book)
        if rate_limited:
            logger.debug("Stopping Google Books strategies early (rate limited)")
            break
        if len(out) >= 20:
            break
    return out


# ── Public API ───────────────────────────────────────────────────────────────


def search_book_candidates(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    *,
    limit: int = 8,
    use_cache: bool = True,
) -> list[BookMetadata]:
    """
    Stage 1 multi-source discovery.

    Returns up to ``limit`` ranked candidates. Raises BookNotFoundError only
    when every strategy returns nothing useful.
    """
    title = (title or "").strip()
    author = (author or "").strip() or None
    if not title and not isbn:
        raise BookNotFoundError("Provide a book title or ISBN.")

    cache = Cache()
    cache_key = book_cache_key(title or "", author, isbn) + f"|{_CACHE_VER}|{limit}"
    if use_cache:
        cached = cache.get(namespace=_CACHE_NS, key=cache_key)
        if isinstance(cached, dict) and cached.get("candidates"):
            try:
                books = [BookMetadata.model_validate(c) for c in cached["candidates"]]
                if books:
                    logger.debug("Candidate cache hit: %s", cache_key)
                    return books
            except Exception:
                pass

    candidates: list[BookMetadata] = []

    with create_client() as client:
        if isbn:
            # Reuse single-ISBN paths via fielded search
            try:
                from chapterscore.books.openlibrary import search_open_library
                from chapterscore.books.googlebooks import search_google_books

                ol = search_open_library(title or "", author, isbn, client=client)
                if ol:
                    candidates.append(ol)
                gb = search_google_books(title or "", author, isbn, client=client)
                if gb:
                    candidates.append(gb)
            except Exception as exc:
                logger.debug("ISBN path failed: %s", exc)

        try:
            candidates.extend(
                search_open_library_candidates(title, author, client=client)
            )
        except Exception as exc:
            logger.debug("OL candidates failed: %s", exc)

        try:
            candidates.extend(
                search_google_books_candidates(title, author, client=client)
            )
        except Exception as exc:
            logger.debug("GB candidates failed: %s", exc)

    ranked = _dedupe_and_rank(
        candidates,
        query_title=title,
        query_author=author,
        limit=limit,
    )

    if not ranked:
        raise BookNotFoundError(
            f"Could not find a strong match for “{title}”.",
            hint=(
                "Try a shorter title fragment, fix spelling, add the author, "
                "or paste an ISBN-13. Stage 1 searches Open Library and Google Books only."
            ),
        )

    # Tag for UI / chapter hint (skip heavy wiki in bulk — optional on confirm only)
    for b in ranked:
        b.raw = {**(b.raw or {}), "quick_lookup": True, "stage1_candidate": True}
        if not b.publisher_blurb and b.description:
            b.publisher_blurb = b.description

    if use_cache:
        cache.set(
            _CACHE_NS,
            cache_key,
            {"candidates": [b.model_dump(mode="json") for b in ranked]},
        )
    return ranked


def lookup_book_quick(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    *,
    use_cache: bool = True,
) -> BookMetadata:
    """
    Backward-compatible single-book quick lookup (best-ranked candidate).

    Prefer ``search_book_candidates`` for the multi-match UI.
    """
    candidates = search_book_candidates(
        title, author=author, isbn=isbn, limit=5, use_cache=use_cache
    )
    best = candidates[0]
    # Optional cheap chapter hint only for the top pick (keeps Stage 1 snappy)
    try:
        from chapterscore.books.wikipedia import quick_chapter_list_available
        from chapterscore.books.http import create_client as _cc

        with _cc() as client:
            hint = quick_chapter_list_available(
                best.title,
                best.authors[0] if best.authors else author,
                client=client,
            )
        best.raw = {**(best.raw or {}), "quick_chapter_hint": bool(hint)}
    except Exception:
        best.raw = {**(best.raw or {}), "quick_chapter_hint": False}
    return best
