"""Tests for FastAPI webhook server."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from git_bot.config import Settings
from git_bot.models.events import EventType, PRReviewEvent
from git_bot.platforms.base import ICodePlatform
from git_bot.server import create_app


@pytest.fixture
def test_settings():
    """Test configuration settings."""
    return Settings(
        gitea_webhook_secret=SecretStr("my-test-secret"),
        _env_file=None,
    )


@pytest.fixture
def mock_platform():
    """Mock platform adapter."""
    platform = AsyncMock(spec=ICodePlatform)
    platform.verify_webhook.return_value = True
    platform.parse_event.return_value = PRReviewEvent(
        event_type=EventType.PR_OPENED,
        platform="gitea",
        repo="testorg/testrepo",
        pr_number=7,
        sender="bob",
        head_sha="sha-head",
        base_sha="sha-base",
    )
    return platform


@pytest.mark.asyncio
async def test_health_check(test_settings, mock_platform):
    """Verify /healthz returns 200 OK."""
    app = create_app(test_settings, mock_platform)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_unauthorized_signature(test_settings, mock_platform):
    """Verify webhook rejects invalid signature with 401."""
    mock_platform.verify_webhook.return_value = False
    app = create_app(test_settings, mock_platform)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/gitea",
            headers={"X-Gitea-Event": "pull_request", "X-Gitea-Signature": "bad"},
            content=b'{"action": "opened"}',
        )
        assert response.status_code == 401
        assert "Invalid webhook signature" in response.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_accepted_and_dispatched(test_settings, mock_platform):
    """Verify valid webhook is accepted immediately with 202."""
    app = create_app(test_settings, mock_platform)

    payload = {"action": "opened"}
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(b"my-test-secret", payload_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/gitea",
            headers={
                "X-Gitea-Event": "pull_request",
                "X-Gitea-Signature": sig,
                "Content-Type": "application/json",
            },
            content=payload_bytes,
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["pr_number"] == 7
        assert data["repo"] == "testorg/testrepo"


@pytest.mark.asyncio
async def test_webhook_comment_accepted_and_dispatched(test_settings, mock_platform):
    """Verify valid comment webhook is accepted with 202 and event type comment."""
    mock_platform.parse_event.return_value = PRReviewEvent(
        event_type=EventType.COMMENT,
        platform="gitea",
        repo="testorg/testrepo",
        pr_number=7,
        sender="alice",
        comment_id=77,
        comment_body="@git_bot please explain line 15",
    )
    app = create_app(test_settings, mock_platform)

    payload = {"action": "created"}
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(b"my-test-secret", payload_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/gitea",
            headers={
                "X-Gitea-Event": "issue_comment",
                "X-Gitea-Signature": sig,
                "Content-Type": "application/json",
            },
            content=payload_bytes,
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "comment"


@pytest.mark.asyncio
async def test_webhook_status_accepted_and_dispatched(test_settings, mock_platform):
    """Verify valid status webhook is accepted with 202 and event type status."""
    mock_platform.parse_event.return_value = PRReviewEvent(
        event_type=EventType.STATUS,
        platform="gitea",
        repo="testorg/testrepo",
        sender="gitea_actions",
        head_sha="head-123",
        status_state="success",
        status_context="ci/build",
    )
    app = create_app(test_settings, mock_platform)

    payload = {"state": "success", "context": "ci/build"}
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(b"my-test-secret", payload_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/gitea",
            headers={
                "X-Gitea-Event": "status",
                "X-Gitea-Signature": sig,
                "Content-Type": "application/json",
            },
            content=payload_bytes,
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "status"
