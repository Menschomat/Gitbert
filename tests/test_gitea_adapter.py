"""Tests for Gitea platform adapter."""

import hashlib
import hmac

import pytest
import respx
from httpx import Response
from pydantic import SecretStr

from git_bot.models.events import EventType
from git_bot.models.platform import CommitState, CommitStatus
from git_bot.models.review import InlineComment, ReviewDecision, ReviewResult
from git_bot.platforms.gitea import GiteaAdapter


@pytest.fixture
def gitea_adapter():
    """Create GiteaAdapter with test credentials."""
    return GiteaAdapter(
        base_url="https://gitea.example.com",
        token=SecretStr("fake-gitea-token"),
        webhook_secret=SecretStr("super-secret-hmac-key"),
    )


def test_verify_webhook_hmac_valid(gitea_adapter):
    """Verify valid HMAC-SHA256 signature passes."""
    body = b'{"action": "opened"}'
    secret = b"super-secret-hmac-key"
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()

    headers = {"X-Gitea-Signature": signature}
    assert gitea_adapter.verify_webhook(headers, body) is True


def test_verify_webhook_hmac_invalid(gitea_adapter):
    """Verify invalid HMAC-SHA256 signature is rejected."""
    body = b'{"action": "opened"}'
    headers = {"X-Gitea-Signature": "invalid-signature"}
    assert gitea_adapter.verify_webhook(headers, body) is False


def test_parse_pr_opened_event(gitea_adapter):
    """Verify parsing Gitea pull_request opened webhook."""
    headers = {"X-Gitea-Event": "pull_request"}
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/repo"},
        "number": 12,
        "sender": {"username": "charlie"},
        "pull_request": {
            "head": {"sha": "head-sha-123"},
            "base": {"sha": "base-sha-456"},
            "draft": False,
        },
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is not None
    assert event.event_type == EventType.PR_OPENED
    assert event.repo == "owner/repo"
    assert event.pr_number == 12
    assert event.head_sha == "head-sha-123"


def test_parse_pr_synchronized_event(gitea_adapter):
    """Verify parsing Gitea pull_request synchronized (commit update) webhook."""
    headers = {"X-Gitea-Event": "pull_request"}
    payload = {
        "action": "synchronized",
        "repository": {"full_name": "owner/repo"},
        "number": 12,
        "sender": {"username": "charlie"},
        "pull_request": {
            "head": {"sha": "new-head-sha-789"},
            "base": {"sha": "base-sha-456"},
            "draft": False,
        },
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is not None
    assert event.event_type == EventType.PR_UPDATED
    assert event.head_sha == "new-head-sha-789"


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_metadata(gitea_adapter):
    """Verify fetching PR metadata and changed files."""
    pr_mock = respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/pulls/12"
    ).mock(
        return_value=Response(
            200,
            json={
                "title": "Fix memory leak",
                "body": "Resolves issue #5",
                "user": {"username": "alice"},
                "base": {"ref": "main", "sha": "base-sha"},
                "head": {"ref": "fix-leak", "sha": "head-sha"},
                "draft": False,
            },
        )
    )
    files_mock = respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/pulls/12/files"
    ).mock(
        return_value=Response(
            200,
            json=[
                {
                    "filename": "src/core.py",
                    "status": "modified",
                    "additions": 5,
                    "deletions": 2,
                }
            ],
        )
    )

    metadata = await gitea_adapter.get_pr_metadata("owner/repo", 12)
    assert pr_mock.called
    assert files_mock.called
    assert metadata.title == "Fix memory leak"
    assert metadata.author == "alice"
    assert len(metadata.changed_files) == 1
    assert metadata.changed_files[0].filename == "src/core.py"


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_diff(gitea_adapter):
    """Verify fetching raw PR diff."""
    raw_diff = "diff --git a/file.py b/file.py\n+print('hello')\n"
    diff_mock = respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/pulls/12.diff"
    ).mock(return_value=Response(200, text=raw_diff))

    diff = await gitea_adapter.get_pr_diff("owner/repo", 12)
    assert diff_mock.called
    assert diff == raw_diff


@pytest.mark.asyncio
@respx.mock
async def test_submit_review(gitea_adapter):
    """Verify submitting PR review to Gitea."""
    review_mock = respx.post(
        "https://gitea.example.com/api/v1/repos/owner/repo/pulls/12/reviews"
    ).mock(return_value=Response(201, json={"id": 99}))

    review = ReviewResult(
        decision=ReviewDecision.APPROVE,
        summary="Code looks great!",
        strengths=["Clean architecture"],
        inline_comments=[
            InlineComment(path="src/main.py", new_position=10, body="Nice cleanup")
        ],
    )
    await gitea_adapter.submit_review("owner/repo", 12, review)
    assert review_mock.called

    sent_body = review_mock.calls.last.request.read().decode("utf-8")
    assert "APPROVED" in sent_body
    assert "Code looks great!" in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_set_commit_status(gitea_adapter):
    """Verify setting commit status in Gitea."""
    status_mock = respx.post(
        "https://gitea.example.com/api/v1/repos/owner/repo/statuses/head-sha"
    ).mock(return_value=Response(201, json={"status": "success"}))

    status = CommitStatus(
        state=CommitState.SUCCESS,
        description="All checks passed",
    )
    await gitea_adapter.set_commit_status("owner/repo", "head-sha", status)
    assert status_mock.called

    sent_body = status_mock.calls.last.request.read().decode("utf-8")
    assert "success" in sent_body
    assert "All checks passed" in sent_body
