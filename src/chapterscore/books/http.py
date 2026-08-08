"""Shared HTTP client for book providers."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from chapterscore.config import get_settings


def create_client(timeout: float | None = None) -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        timeout=timeout or settings.chapterscore_http_timeout,
        headers={
            "User-Agent": settings.chapterscore_user_agent,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        follow_redirects=True,
    )


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.8, min=0.5, max=8),
    reraise=True,
)
def get_json(client: httpx.Client, url: str, *, params: dict | None = None) -> dict | list:
    response = client.get(url, params=params)
    # Retry on 429 / 5xx
    if response.status_code == 429 or response.status_code >= 500:
        response.raise_for_status()
    response.raise_for_status()
    return response.json()


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.8, min=0.5, max=8),
    reraise=True,
)
def get_text(client: httpx.Client, url: str, *, params: dict | None = None) -> str:
    response = client.get(url, params=params)
    if response.status_code == 429 or response.status_code >= 500:
        response.raise_for_status()
    response.raise_for_status()
    return response.text
