"""Cache provider factory selecting Redis/Valkey or Memory fallback."""

from gitbert.cache.base import ICacheProvider
from gitbert.cache.memory import MemoryCacheProvider
from gitbert.config import Settings, get_settings


def get_cache_provider(app_settings: Settings | None = None) -> ICacheProvider:
    """Instantiate appropriate cache provider based on settings."""
    cfg = app_settings or get_settings()
    if cfg.redis_url:
        from gitbert.cache.redis import RedisCacheProvider

        return RedisCacheProvider(redis_url=cfg.redis_url)
    return MemoryCacheProvider()
