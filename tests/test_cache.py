"""Unit tests for pluggable review caching (Memory and Valkey/Redis)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from gitbert.cache.factory import get_cache_provider
from gitbert.cache.memory import MemoryCacheProvider
from gitbert.cache.redis import RedisCacheProvider
from gitbert.config import Settings
from gitbert.models.review import InlineComment, ReviewDecision, ReviewResult


@pytest.fixture
def sample_review() -> ReviewResult:
    """Fixture producing a sample ReviewResult."""
    return ReviewResult(
        decision=ReviewDecision.APPROVE,
        summary="Code looks great!",
        strengths=["Clean architecture", "Tests added"],
        risks_or_concerns=[],
        inline_comments=[
            InlineComment(
                path="src/main.py",
                new_position=10,
                body="Nice clean implementation.",
            )
        ],
    )


@pytest.mark.asyncio
async def test_memory_cache_set_and_get(sample_review):
    """Verify storing and retrieving ReviewResult from in-memory cache."""
    cache = MemoryCacheProvider()
    await cache.set("owner/repo:1", sample_review, ttl_seconds=60)

    cached = await cache.get("owner/repo:1")
    assert cached is not None
    assert cached.decision == ReviewDecision.APPROVE
    assert cached.summary == "Code looks great!"
    assert len(cached.inline_comments) == 1
    assert cached.inline_comments[0].path == "src/main.py"


@pytest.mark.asyncio
async def test_memory_cache_expiration(sample_review):
    """Verify expired items return None in in-memory cache."""
    cache = MemoryCacheProvider()
    # Store with zero TTL so it expires immediately
    await cache.set("owner/repo:1", sample_review, ttl_seconds=0)
    await asyncio.sleep(0.01)

    cached = await cache.get("owner/repo:1")
    assert cached is None


@pytest.mark.asyncio
async def test_memory_cache_delete(sample_review):
    """Verify deleting keys from in-memory cache."""
    cache = MemoryCacheProvider()
    await cache.set("owner/repo:1", sample_review)
    await cache.delete("owner/repo:1")

    cached = await cache.get("owner/repo:1")
    assert cached is None


@pytest.mark.asyncio
async def test_memory_cache_bounded_capacity(sample_review):
    """Verify in-memory cache enforces max_items limit."""
    cache = MemoryCacheProvider(max_items=2)
    await cache.set("k1", sample_review)
    await cache.set("k2", sample_review)
    await cache.set("k3", sample_review)

    # Oldest key 'k1' should have been evicted
    assert await cache.get("k1") is None
    assert await cache.get("k2") is not None
    assert await cache.get("k3") is not None


@pytest.mark.asyncio
async def test_redis_cache_set_and_get(sample_review):
    """Verify serialization and deserialization with RedisCacheProvider."""
    mock_redis = AsyncMock()
    mock_redis.get.return_value = sample_review.model_dump_json()

    cache = RedisCacheProvider("redis://localhost:6379/0", client=mock_redis)
    await cache.set("owner/repo:1", sample_review, ttl_seconds=3600)

    assert mock_redis.set.called
    call_args = mock_redis.set.call_args
    assert call_args[0][0] == "owner/repo:1"
    assert "Code looks great!" in call_args[0][1]
    assert call_args[1]["ex"] == 3600

    cached = await cache.get("owner/repo:1")
    assert cached is not None
    assert cached.decision == ReviewDecision.APPROVE
    assert cached.summary == "Code looks great!"


@pytest.mark.asyncio
async def test_redis_cache_get_missing_or_error():
    """Verify missing keys and Redis connection errors return None safely."""
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    cache = RedisCacheProvider("redis://localhost:6379/0", client=mock_redis)
    assert await cache.get("missing-key") is None

    # Error simulation
    mock_redis.get.side_effect = ConnectionError("Redis is unreachable")
    assert await cache.get("error-key") is None


@pytest.mark.asyncio
async def test_redis_cache_delete():
    """Verify delete operation on RedisCacheProvider."""
    mock_redis = AsyncMock()
    cache = RedisCacheProvider("redis://localhost:6379/0", client=mock_redis)
    await cache.delete("test-key")
    mock_redis.delete.assert_called_once_with("test-key")


def test_cache_factory_selection():
    """Verify factory returns appropriate cache provider based on settings."""
    settings_default = Settings(_env_file=None)
    provider_default = get_cache_provider(settings_default)
    assert isinstance(provider_default, MemoryCacheProvider)

    settings_redis = Settings(redis_url="redis://my-valkey:6379/0", _env_file=None)
    provider_redis = get_cache_provider(settings_redis)
    assert isinstance(provider_redis, RedisCacheProvider)
    assert provider_redis.redis_url == "redis://my-valkey:6379/0"
