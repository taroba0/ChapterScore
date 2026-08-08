"""Simple JSON file cache for book metadata and vibe analyses."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from chapterscore.config import get_settings

T = TypeVar("T", bound=BaseModel)


def _key_hash(namespace: str, key: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{key}".encode()).hexdigest()[:24]
    return f"{namespace}_{digest}"


class Cache:
    """Disk-backed cache with TTL. Stores Pydantic models or plain dicts as JSON."""

    def __init__(self, root: Path | None = None, ttl_hours: float | None = None) -> None:
        settings = get_settings()
        self.root = root or (settings.cache_dir / "entries")
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = (ttl_hours if ttl_hours is not None else settings.chapterscore_cache_ttl_hours) * 3600

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / f"{_key_hash(namespace, key)}.json"

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        expires_at = payload.get("_expires_at")
        if expires_at is not None and time.time() > float(expires_at):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return payload.get("data")

    def get_model(self, namespace: str, key: str, model: type[T]) -> T | None:
        data = self.get(namespace, key)
        if data is None:
            return None
        try:
            return model.model_validate(data)
        except Exception:
            return None

    def set(self, namespace: str, key: str, data: BaseModel | dict[str, Any]) -> None:
        if isinstance(data, BaseModel):
            serialized = data.model_dump(mode="json")
        else:
            serialized = data
        payload = {
            "_cached_at": time.time(),
            "_expires_at": time.time() + self.ttl_seconds,
            "_key": key,
            "_namespace": namespace,
            "data": serialized,
        }
        path = self._path(namespace, key)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def delete(self, namespace: str, key: str) -> None:
        self._path(namespace, key).unlink(missing_ok=True)

    def clear(self, namespace: str | None = None) -> int:
        removed = 0
        for path in self.root.glob("*.json"):
            if namespace and not path.name.startswith(f"{namespace}_"):
                # hash-based names use namespace_ prefix from _key_hash
                if not path.name.startswith(namespace):
                    continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed


def book_cache_key(title: str, author: str | None = None, isbn: str | None = None) -> str:
    parts = [title.strip().lower()]
    if author:
        parts.append(author.strip().lower())
    if isbn:
        parts.append(isbn.strip().replace("-", ""))
    return "|".join(parts)
