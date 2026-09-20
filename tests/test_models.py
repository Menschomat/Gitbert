"""Tests for domain models."""

import pytest
from pydantic import ValidationError

from gitbert.models.events import EventType, PRReviewEvent
from gitbert.models.platform import ChangedFile, CommitState, CommitStatus, PRMetadata
from gitbert.models.review import InlineComment, ReviewDecision, ReviewResult


def test_commit_status_validation():
    """Verify CommitStatus initializes and validates states."""
    status = CommitStatus(
        state=CommitState.SUCCESS,
        description="All checks passed",
    )
    assert status.state == CommitState.SUCCESS
    assert status.context == "gitbert/pr-review"
    assert status.description == "All checks passed"


def test_pr_metadata_validation():
    """Verify PRMetadata model parses correctly with changed files."""
    pr = PRMetadata(
        repo="octocat/hello-world",
        number=42,
        title="Add greetings feature",
        author="alice",
        base_ref="main",
        base_sha="abc1234",
        head_ref="feature/greetings",
        head_sha="def5678",
        changed_files=[
            ChangedFile(filename="src/hello.py", status="added", additions=10)
        ],
    )
    assert pr.repo == "octocat/hello-world"
    assert pr.number == 42
    assert len(pr.changed_files) == 1
    assert pr.changed_files[0].filename == "src/hello.py"


def test_review_result_serialization():
    """Verify ReviewResult model serialization and inline comment structure."""
    result = ReviewResult(
        decision=ReviewDecision.REQUEST_CHANGES,
        summary="Found potential SQL injection vulnerability.",
        risks_or_concerns=["Raw string formatting in SQL query at line 24."],
        inline_comments=[
            InlineComment(
                path="src/db.py",
                new_position=24,
                body="Use parameterized query instead of string formatting.",
            )
        ],
    )
    assert result.decision == ReviewDecision.REQUEST_CHANGES
    assert len(result.inline_comments) == 1
    assert result.inline_comments[0].path == "src/db.py"

    dump = result.model_dump()
    assert dump["decision"] == "REQUEST_CHANGES"
    assert dump["inline_comments"][0]["new_position"] == 24


def test_invalid_review_decision_raises():
    """Verify invalid decision enum raises ValidationError."""
    with pytest.raises(ValidationError):
        ReviewResult(
            decision="INVALID_DECISION",  # type: ignore[arg-type]
            summary="Test",
        )


def test_pr_review_event():
    """Verify PRReviewEvent holds normalized webhook event data."""
    event = PRReviewEvent(
        event_type=EventType.PR_OPENED,
        platform="gitea",
        repo="org/project",
        pr_number=1,
        sender="bob",
        head_sha="sha-head",
        base_sha="sha-base",
    )
    assert event.event_type == EventType.PR_OPENED
    assert event.platform == "gitea"
    assert event.pr_number == 1


def test_pr_comment_model():
    """Verify PRComment model validation and defaults."""
    from datetime import UTC, datetime

    from gitbert.models.comments import CommentType, PRComment

    now = datetime.now(UTC)
    comment = PRComment(
        id=101,
        author="git_bot",
        is_bot=True,
        comment_type=CommentType.REVIEW_COMMENT,
        body="This is an automated review comment.",
        created_at=now,
        path="src/main.py",
        line=15,
    )
    assert comment.id == 101
    assert comment.is_bot is True
    assert comment.comment_type == CommentType.REVIEW_COMMENT
    assert comment.path == "src/main.py"
    assert comment.line == 15


def test_comment_response_model():
    """Verify CommentResponse model validation."""
    from gitbert.models.review import CommentResponse

    resp = CommentResponse(
        should_reply=True,
        reply="You can catch this with `try ... except FileNotFoundError:`.",
        reasoning="User asked for code example on how to handle the exception.",
    )
    assert resp.should_reply is True
    assert "FileNotFoundError" in resp.reply  # type: ignore[operator]
    assert resp.reasoning != ""


def test_action_diagnostic_result_model():
    """Verify ActionDiagnosticResult model validation."""
    from gitbert.models.events import EventType, PRReviewEvent
    from gitbert.models.review import ActionDiagnosticResult

    diag = ActionDiagnosticResult(
        context="test-python3.14",
        diagnosis="Pytest failed on tests/test_auth.py with ModuleNotFoundError.",
        suggested_fix="Add missing dependency to pyproject.toml.",
        related_files=["pyproject.toml", "tests/test_auth.py"],
    )
    assert diag.context == "test-python3.14"
    assert len(diag.related_files) == 2

    status_event = PRReviewEvent(
        event_type=EventType.STATUS,
        platform="gitea",
        repo="owner/repo",
        sender="ci_runner",
        head_sha="head-123",
        status_state="failure",
        status_context="test-python3.14",
        target_url="https://gitea.example.com/owner/repo/actions/runs/1",
    )
    assert status_event.event_type == EventType.STATUS
    assert status_event.status_state == "failure"
