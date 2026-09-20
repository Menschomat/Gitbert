"""Abstract base class for review caching providers."""

from abc import ABC, abstractmethod

from gitbert.models.review import ReviewResult


class ICacheProvider(ABC):
    """Interface for review caching backends."""

    @abstractmethod
    async def get(self, key: str) -> ReviewResult | None:
        """Retrieve a cached ReviewResult by key. Returns None if missing or expired."""
        pass

    @abstractmethod
    async def set(
        self, key: str, value: ReviewResult, ttl_seconds: int = 86400
    ) -> None:
        """Store a ReviewResult with an expiration TTL in seconds."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a cached key."""
        pass

    @abstractmethod
    async def aclose(self) -> None:
        """Clean up underlying connections or background tasks."""
        pass
