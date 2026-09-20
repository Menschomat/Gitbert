"""In-memory cache provider with TTL expiration and bounded capacity."""

import time

from gitbert.cache.base import ICacheProvider
from gitbert.models.review import ReviewResult


class MemoryCacheProvider(ICacheProvider):
    """In-memory cache provider with TTL expiration for single-process deployments."""

    def __init__(self, max_items: int = 1000):
        self._store: dict[str, tuple[ReviewResult, float]] = {}
        self._max_items = max_items

    def _purge_expired(self) -> None:
        """Purge all expired items from internal store."""
        now = time.time()
        expired_keys = [k for k, (_, exp) in self._store.items() if now >= exp]
        for k in expired_keys:
            self._store.pop(k, None)

    async def get(self, key: str) -> ReviewResult | None:
        """Retrieve cached ReviewResult if present and not expired."""
        entry = self._store.get(key)
        if entry is None:
            return None

        result, expire_at = entry
        if time.time() >= expire_at:
            self._store.pop(key, None)
            return None

        return result

    async def set(
        self, key: str, value: ReviewResult, ttl_seconds: int = 86400
    ) -> None:
        """Store ReviewResult with TTL."""
        # Evict expired items if approaching maximum capacity
        if len(self._store) >= self._max_items:
            self._purge_expired()

        # If still at max capacity, evict oldest entry
        if len(self._store) >= self._max_items:
            oldest_key = next(iter(self._store))
            self._store.pop(oldest_key, None)

        expire_at = time.time() + ttl_seconds
        self._store[key] = (value, expire_at)

    async def delete(self, key: str) -> None:
        """Remove a cached key."""
        self._store.pop(key, None)

    async def aclose(self) -> None:
        """Clear memory cache on shutdown."""
        self._store.clear()
