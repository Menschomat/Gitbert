"""Tests for the review orchestrator engine."""

from unittest.mock import AsyncMock

import pytest

from gitbert.config import ReviewMode, Settings
from gitbert.models.events import EventType, PRReviewEvent
from gitbert.models.platform import ChangedFile, CommitState, CommitStatus, PRMetadata
from gitbert.models.review import ReviewDecision, ReviewResult
from gitbert.orchestrator.engine import ReviewEngine
from gitbert.platforms.base import ICodePlatform


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
    platform.get_commit_statuses.return_value = []
    platform.find_pr_for_commit.return_value = 15
    platform.get_action_log.return_value = ""
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


@pytest.mark.asyncio
async def test_engine_process_comment_event_mentioned(mock_platform):
    """Verify engine responds when bot is mentioned in a comment."""
    engine = ReviewEngine(platform=mock_platform)
    event = PRReviewEvent(
        event_type=EventType.COMMENT,
        platform="gitea",
        repo="owner/repo",
        pr_number=15,
        sender="alice",
        comment_id=501,
        comment_body="@git_bot can you review the latest change?",
    )

    result = await engine.process_event(event)
    assert result is None
    mock_platform.post_pr_comment.assert_called_once()
    call_args = mock_platform.post_pr_comment.call_args[0]
    assert call_args[0] == "owner/repo"
    assert call_args[1] == 15
    assert "alice" in call_args[2]


@pytest.mark.asyncio
async def test_engine_process_comment_mention_only_ignored(mock_platform):
    """Verify engine ignores unmentioned comments when in MENTION_ONLY mode."""
    from gitbert.config import CommentTriggerMode

    settings = Settings(
        comment_trigger_mode=CommentTriggerMode.MENTION_ONLY,
        _env_file=None,
    )
    engine = ReviewEngine(platform=mock_platform, app_settings=settings)
    event = PRReviewEvent(
        event_type=EventType.COMMENT,
        platform="gitea",
        repo="owner/repo",
        pr_number=15,
        sender="alice",
        comment_id=502,
        comment_body="Just updated the documentation.",
    )

    await engine.process_event(event)
    mock_platform.post_pr_comment.assert_not_called()


@pytest.mark.asyncio
async def test_engine_process_comment_custom_responder(mock_platform):
    """Verify custom comment responder is invoked and posts reply."""
    from gitbert.models.review import CommentResponse

    mock_responder = AsyncMock(
        return_value=CommentResponse(
            should_reply=True,
            reply="The suggested timeout is 30 seconds.",
            reasoning="Helpful technical answer.",
        )
    )
    engine = ReviewEngine(
        platform=mock_platform,
        responder=mock_responder,
    )
    event = PRReviewEvent(
        event_type=EventType.COMMENT,
        platform="gitea",
        repo="owner/repo",
        pr_number=15,
        sender="alice",
        comment_id=503,
        comment_body="What timeout should we set?",
    )

    await engine.process_event(event)
    mock_responder.assert_called_once_with(event)
    mock_platform.post_pr_comment.assert_called_once_with(
        "owner/repo", 15, "The suggested timeout is 30 seconds."
    )


@pytest.mark.asyncio
async def test_engine_process_opened_event_with_running_actions(mock_platform):
    """Verify two-phase mode: advisory comments when Actions are pending."""
    # External CI action is currently PENDING
    mock_platform.get_commit_statuses.return_value = [
        CommitStatus(
            state=CommitState.PENDING,
            context="ci/build",
            description="Build in progress",
        )
    ]
    engine = ReviewEngine(platform=mock_platform)
    event = PRReviewEvent(
        event_type=EventType.PR_OPENED,
        platform="gitea",
        repo="owner/repo",
        pr_number=15,
        sender="alice",
        head_sha="head-222",
        base_sha="base-111",
    )

    review = await engine.process_event(event)
    assert review is not None

    # Verify review was submitted as advisory COMMENT
    submitted_review = mock_platform.submit_review.call_args[0][2]
    assert submitted_review.decision == ReviewDecision.COMMENT
    assert "Notice" in submitted_review.summary

    # Status check remains PENDING
    final_status = mock_platform.set_commit_status.call_args[0][2]
    assert final_status.state == CommitState.PENDING
    assert "awaiting CI" in final_status.description


@pytest.mark.asyncio
async def test_engine_status_event_all_succeeded(mock_platform):
    """Verify finalizing review to SUCCESS when all CI actions finish cleanly."""
    engine = ReviewEngine(platform=mock_platform)

    # Seed the cached review
    await engine.cache.set(
        "owner/repo:15",
        ReviewResult(
            decision=ReviewDecision.APPROVE,
            summary="Clean code",
        ),
    )

    # CI checks are now all SUCCESS
    mock_platform.get_commit_statuses.return_value = [
        CommitStatus(
            state=CommitState.SUCCESS,
            context="ci/build",
            description="Build succeeded",
        )
    ]

    event = PRReviewEvent(
        event_type=EventType.STATUS,
        platform="gitea",
        repo="owner/repo",
        sender="gitea_actions",
        head_sha="head-222",
        status_state="success",
        status_context="ci/build",
    )

    await engine.process_event(event)

    # Status check updated to SUCCESS
    last_status = mock_platform.set_commit_status.call_args[0][2]
    assert last_status.state == CommitState.SUCCESS
    assert "passed" in last_status.description


@pytest.mark.asyncio
async def test_engine_status_event_failed_with_diagnostics(mock_platform):
    """Verify diagnosing failure and posting advice when CI action fails."""
    from gitbert.models.review import ActionDiagnosticResult

    mock_platform.get_commit_statuses.return_value = [
        CommitStatus(
            state=CommitState.FAILURE,
            context="ci/test",
            description="Tests failed",
            target_url="https://gitea.example.com/logs/job-1",
        )
    ]
    mock_platform.get_action_log.return_value = "AssertionError: expected 200 got 500"

    mock_diagnostician = AsyncMock(
        return_value=ActionDiagnosticResult(
            context="ci/test",
            diagnosis="Database connection refused during integration test.",
            suggested_fix="Set TEST_DB_HOST=localhost in CI environment.",
            related_files=["src/db.py"],
        )
    )

    engine = ReviewEngine(
        platform=mock_platform,
        diagnostician=mock_diagnostician,
    )

    event = PRReviewEvent(
        event_type=EventType.STATUS,
        platform="gitea",
        repo="owner/repo",
        sender="gitea_actions",
        head_sha="head-222",
        status_state="failure",
        status_context="ci/test",
    )

    await engine.process_event(event)

    # Diagnostician called
    mock_diagnostician.assert_called_once()

    # Diagnostic advice posted to PR
    mock_platform.post_pr_comment.assert_called_once()
    comment_body = mock_platform.post_pr_comment.call_args[0][2]
    assert "CI Action Failed: `ci/test`" in comment_body
    assert "Database connection refused" in comment_body
    assert "Set TEST_DB_HOST=localhost" in comment_body

    # Status check updated to FAILURE
    last_status = mock_platform.set_commit_status.call_args[0][2]
    assert last_status.state == CommitState.FAILURE


@pytest.mark.asyncio
async def test_engine_analyze_pr_litellm(mock_platform):
    """Verify analyze_pr uses litellm when model_provider is litellm."""
    import json
    from unittest.mock import MagicMock, patch

    from gitbert.config import ModelProvider, Settings
    from gitbert.security.context import ScopedMRContext

    settings = Settings(
        model_provider=ModelProvider.LITELLM,
        openai_compatible_api_key="test-key",
        openai_compatible_model="openrouter/anthropic/claude-3.5-sonnet",
        _env_file=None,
    )
    engine = ReviewEngine(platform=mock_platform, app_settings=settings)
    context = ScopedMRContext(
        platform="gitea",
        repo="owner/repo",
        pr_number=10,
        head_sha="sha123",
        base_sha="sha456",
        allowed_files=frozenset(["src/app.py"]),
    )

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(
        {
            "decision": "APPROVED",
            "summary": "Looks great from Claude via OpenRouter.",
            "strengths": ["Clean code"],
            "risks_or_concerns": [],
            "inline_comments": [],
        }
    )
    mock_litellm_resp = MagicMock(choices=[mock_choice])

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=mock_litellm_resp
    ) as mock_acompletion:
        result = await engine.analyze_pr(context, "+ print('hello')")
        assert result.decision == ReviewDecision.APPROVE
        assert "Claude" in result.summary
        mock_acompletion.assert_called_once()
        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["model"] == "openrouter/anthropic/claude-3.5-sonnet"
        assert call_kwargs["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_engine_comment_reply_litellm(mock_platform):
    """Verify comment evaluation uses litellm when configured."""
    import json
    from unittest.mock import MagicMock, patch

    from gitbert.config import ModelProvider, Settings

    settings = Settings(
        model_provider=ModelProvider.LITELLM,
        openai_compatible_api_key="test-key",
        openai_compatible_model="openrouter/google/gemini-2.0-flash",
        _env_file=None,
    )
    mock_platform.get_pr_comments.return_value = []
    engine = ReviewEngine(platform=mock_platform, app_settings=settings)

    event = PRReviewEvent(
        event_type=EventType.COMMENT,
        platform="gitea",
        repo="owner/repo",
        pr_number=10,
        sender="alice",
        comment_id=1,
        comment_body="How do I configure the database?",
    )

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(
        {
            "should_reply": True,
            "reply": "You can set DATABASE_URL in your .env file.",
            "reasoning": "Technical query answered.",
        }
    )
    mock_litellm_resp = MagicMock(choices=[mock_choice])

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=mock_litellm_resp
    ):
        response = await engine.evaluate_and_reply_comment(event)
        assert response is not None
        assert response.should_reply is True
        assert "DATABASE_URL" in response.reply
        mock_platform.post_pr_comment.assert_called_once()


@pytest.mark.asyncio
async def test_engine_action_diagnosis_litellm(mock_platform):
    """Verify action diagnosis uses litellm when configured."""
    import json
    from unittest.mock import MagicMock, patch

    from gitbert.config import ModelProvider, Settings

    settings = Settings(
        model_provider=ModelProvider.LITELLM,
        openai_compatible_api_key="test-key",
        openai_compatible_model="openrouter/anthropic/claude-3.5-sonnet",
        _env_file=None,
    )
    engine = ReviewEngine(platform=mock_platform, app_settings=settings)

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(
        {
            "context": "build/docker",
            "diagnosis": "Missing apt package libpq-dev",
            "suggested_fix": "Add RUN apt-get install -y libpq-dev to Dockerfile",
            "related_files": ["Dockerfile"],
        }
    )
    mock_litellm_resp = MagicMock(choices=[mock_choice])

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=mock_litellm_resp
    ):
        diag = await engine.diagnose_action_failure(
            "owner/repo", 10, "build/docker", "error: libpq-dev not found"
        )
        assert diag is not None
        assert "libpq-dev" in diag.diagnosis
        assert "Dockerfile" in diag.related_files
