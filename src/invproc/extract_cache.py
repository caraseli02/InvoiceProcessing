"""In-memory cache for extracted invoice payloads."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExtractCacheEntry:
    """Cache payload metadata."""

    payload: dict
    expires_at: float
    hit_count: int


class InMemoryExtractCache:
    """Thread-safe TTL + LRU cache for extract responses."""

    def __init__(self, *, ttl_sec: int, max_entries: int) -> None:
        self._lock = threading.Lock()
        self._ttl_sec = ttl_sec
        self._max_entries = max_entries
        self._entries: OrderedDict[str, ExtractCacheEntry] = OrderedDict()

    def configure(self, *, ttl_sec: int, max_entries: int) -> None:
        """Apply runtime cache limits from config."""
        with self._lock:
            self._ttl_sec = ttl_sec
            self._max_entries = max_entries
            self._prune_expired_locked()
            self._prune_capacity_locked()

    def get(self, key: str) -> Optional[dict]:
        """Return cached payload if present and not expired."""
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None

            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None

            # Mark as recently used and update observability counter.
            updated = ExtractCacheEntry(
                payload=entry.payload,
                expires_at=entry.expires_at,
                hit_count=entry.hit_count + 1,
            )
            self._entries[key] = updated
            self._entries.move_to_end(key)
            return updated.payload

    def set(self, key: str, payload: dict) -> None:
        """Insert/update payload and enforce TTL/capacity bounds."""
        now = time.time()
        with self._lock:
            self._prune_expired_locked(now=now)
            self._entries[key] = ExtractCacheEntry(
                payload=payload,
                expires_at=now + self._ttl_sec,
                hit_count=0,
            )
            self._entries.move_to_end(key)
            self._prune_capacity_locked()

    def reset(self) -> None:
        """Clear cache state (used by tests)."""
        with self._lock:
            self._entries.clear()

    def _prune_expired_locked(self, *, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()

        expired_keys = [
            key for key, entry in self._entries.items() if entry.expires_at <= now
        ]
        for key in expired_keys:
            self._entries.pop(key, None)

    def _prune_capacity_locked(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
