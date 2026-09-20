"""Valkey and Redis cache provider using redis.asyncio."""

import logging
from typing import Any

from redis.asyncio import Redis

from gitbert.cache.base import ICacheProvider
from gitbert.models.review import ReviewResult

logger = logging.getLogger(__name__)


class RedisCacheProvider(ICacheProvider):
    """Valkey/Redis cache provider supporting distributed workers and persistence."""

    def __init__(self, redis_url: str, client: Any | None = None):
        self.redis_url = redis_url
        self._client: Redis = client or Redis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str) -> ReviewResult | None:
        """Retrieve and deserialize ReviewResult from Valkey/Redis."""
        try:
            raw = await self._client.get(key)
            if not raw:
                return None
            return ReviewResult.model_validate_json(raw)
        except Exception as exc:
            logger.warning("RedisCache: Failed to get key '%s': %s", key, exc)
            return None

    async def set(
        self, key: str, value: ReviewResult, ttl_seconds: int = 86400
    ) -> None:
        """Serialize and store ReviewResult with TTL expiration in Valkey/Redis."""
        try:
            payload = value.model_dump_json()
            await self._client.set(key, payload, ex=ttl_seconds)
        except Exception as exc:
            logger.error("RedisCache: Failed to set key '%s': %s", key, exc)

    async def delete(self, key: str) -> None:
        """Delete key from Valkey/Redis."""
        try:
            await self._client.delete(key)
        except Exception as exc:
            logger.warning("RedisCache: Failed to delete key '%s': %s", key, exc)

    async def aclose(self) -> None:
        """Close connection to Valkey/Redis."""
        try:
            await self._client.aclose()
        except Exception as exc:
            logger.debug("RedisCache: Error closing Redis connection: %s", exc)
