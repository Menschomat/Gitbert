"""Tests for the review orchestrator engine."""

from unittest.mock import AsyncMock

import pytest

from git_bot.config import ReviewMode, Settings
from git_bot.models.events import EventType, PRReviewEvent
from git_bot.models.platform import ChangedFile, CommitState, PRMetadata
from git_bot.models.review import ReviewDecision, ReviewResult
from git_bot.orchestrator.engine import ReviewEngine
from git_bot.platforms.base import ICodePlatform


@pytest.fixture
def mock_platform():
    """Mock platform with sample PR metadata and diff."""
    platform = AsyncMock(spec=ICodePlatform)
    platform.get_pr_metadata.return_value = PRMetadata(
        repo="owner/repo",
        number=15,
        title="Refactor auth",
        author="alice",
        base_ref="main",
        base_sha="base-111",
        head_ref="refactor",
        head_sha="head-222",
        changed_files=[ChangedFile(filename="src/auth.py")],
    )
    platform.get_pr_diff.return_value = (
        "diff --git a/src/auth.py b/src/auth.py\n+def login(): pass\n"
    )
    return platform


@pytest.mark.asyncio
async def test_engine_process_ignored_event(mock_platform):
    """Verify engine ignores events with IGNORED type."""
    engine = ReviewEngine(platform=mock_platform)
    event = PRReviewEvent(
        event_type=EventType.IGNORED,
        platform="gitea",
        repo="owner/repo",
        pr_number=15,
        sender="bot",
        head_sha="head-222",
        base_sha="base-111",
    )
    result = await engine.process_event(event)
    assert result is None
    mock_platform.set_commit_status.assert_not_called()


@pytest.mark.asyncio
async def test_engine_process_opened_event(mock_platform):
    """Verify engine sets pending status, analyzes PR, and publishes review."""
    custom_settings = Settings(
        review_mode=ReviewMode.ADVISORY,
        _env_file=None,
    )
    engine = ReviewEngine(platform=mock_platform, app_settings=custom_settings)

    event = PRReviewEvent(
        event_type=EventType.PR_OPENED,
        platform="gitea",
        repo="owner/repo",
        pr_number=15,
        sender="alice",
        head_sha="head-222",
        base_sha="base-111",
    )

    # Inject mock analyzer function to simulate LLM review verdict
    expected_review = ReviewResult(
        decision=ReviewDecision.APPROVE,
        summary="Refactoring looks clean and sound.",
        strengths=["Modular functions"],
    )
    engine.analyze_pr = AsyncMock(return_value=expected_review)

    result = await engine.process_event(event)

    # 1. Verify initial status was set to PENDING
    assert mock_platform.set_commit_status.call_count == 2
    first_status = mock_platform.set_commit_status.call_args_list[0][0][2]
    assert first_status.state == CommitState.PENDING

    # 2. Verify review was published to platform
    mock_platform.submit_review.assert_called_once()
    assert result == expected_review
