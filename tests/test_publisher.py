"""Tests for deterministic review publisher."""

from unittest.mock import AsyncMock

import pytest

from git_bot.config import ReviewMode
from git_bot.models.platform import CommitState
from git_bot.models.review import InlineComment, ReviewDecision, ReviewResult
from git_bot.orchestrator.publisher import ReviewPublisher
from git_bot.platforms.base import ICodePlatform


@pytest.fixture
def mock_platform():
    """Mock platform adapter."""
    return AsyncMock(spec=ICodePlatform)


@pytest.mark.asyncio
async def test_publisher_advisory_mode(mock_platform):
    """Verify advisory mode posts review as COMMENT and sets commit status."""
    publisher = ReviewPublisher(platform=mock_platform, mode=ReviewMode.ADVISORY)

    review = ReviewResult(
        decision=ReviewDecision.REQUEST_CHANGES,
        summary="Found critical bug in authorization.",
        risks_or_concerns=["Missing auth check"],
        inline_comments=[
            InlineComment(path="src/auth.py", new_position=12, body="Check token")
        ],
    )

    await publisher.publish(
        repo="myorg/repo",
        pr_number=10,
        head_sha="head-123",
        review=review,
    )

    # 1. Commit status check should be set to FAILURE
    mock_platform.set_commit_status.assert_called_once()
    status_call_args = mock_platform.set_commit_status.call_args[0]
    assert status_call_args[0] == "myorg/repo"
    assert status_call_args[1] == "head-123"
    assert status_call_args[2].state == CommitState.FAILURE

    # 2. In advisory mode, review event sent to platform must be COMMENT
    mock_platform.submit_review.assert_called_once()
    review_call_args = mock_platform.submit_review.call_args[0]
    submitted_review = review_call_args[2]
    assert submitted_review.decision == ReviewDecision.COMMENT
    assert "ADVISORY VERDICT" in submitted_review.summary


@pytest.mark.asyncio
async def test_publisher_enforcing_mode_approved(mock_platform):
    """Verify enforcing mode submits native APPROVED review and SUCCESS status."""
    publisher = ReviewPublisher(platform=mock_platform, mode=ReviewMode.ENFORCING)

    review = ReviewResult(
        decision=ReviewDecision.APPROVE,
        summary="All good!",
        strengths=["Clean implementation"],
    )

    await publisher.publish(
        repo="myorg/repo",
        pr_number=10,
        head_sha="head-123",
        review=review,
    )

    # Commit status check should be SUCCESS
    status_call_args = mock_platform.set_commit_status.call_args[0]
    assert status_call_args[2].state == CommitState.SUCCESS

    # Native APPROVED review submitted
    review_call_args = mock_platform.submit_review.call_args[0]
    submitted_review = review_call_args[2]
    assert submitted_review.decision == ReviewDecision.APPROVE
