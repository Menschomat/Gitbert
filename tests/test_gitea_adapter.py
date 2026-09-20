"""Tests for Gitea platform adapter."""

import hashlib
import hmac

import pytest
import respx
from httpx import Response
from pydantic import SecretStr

from gitbert.models.events import EventType
from gitbert.models.platform import CommitState, CommitStatus
from gitbert.models.review import InlineComment, ReviewDecision, ReviewResult
from gitbert.platforms.gitea import GiteaAdapter


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


@pytest.mark.asyncio
@respx.mock
async def test_list_directory(gitea_adapter):
    """Verify listing repository directory contents."""
    contents_mock = respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/contents/src"
    ).mock(
        return_value=Response(
            200,
            json=[
                {
                    "name": "models.py",
                    "path": "src/models.py",
                    "type": "file",
                    "size": 120,
                },
                {"name": "utils", "path": "src/utils", "type": "dir", "size": 0},
            ],
        )
    )

    items = await gitea_adapter.list_directory("owner/repo", "src", "head-sha")
    assert contents_mock.called
    assert len(items) == 2
    assert items[0]["name"] == "models.py"
    assert items[1]["type"] == "dir"


def test_parse_issue_comment_event(gitea_adapter):
    """Verify parsing Gitea issue_comment on a PR."""
    headers = {"X-Gitea-Event": "issue_comment"}
    payload = {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "sender": {"username": "developer_alice"},
        "issue": {
            "number": 42,
            "pull_request": {"head": {"sha": "head-123"}},
        },
        "comment": {
            "id": 999,
            "body": "@git_bot what do you think about this refactoring?",
        },
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is not None
    assert event.event_type == EventType.COMMENT
    assert event.pr_number == 42
    assert event.sender == "developer_alice"
    assert event.comment_id == 999
    assert "@git_bot" in event.comment_body


def test_parse_comment_loop_prevention(gitea_adapter):
    """Verify bot's own comments are strictly ignored to prevent infinite loops."""
    headers = {"X-Gitea-Event": "issue_comment"}
    payload = {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "sender": {"username": "Gitbert"},  # Same as adapter's bot_name
        "issue": {"number": 42, "pull_request": {}},
        "comment": {"id": 1001, "body": "I am answering my own comment"},
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is not None
    assert event.event_type == EventType.IGNORED


def test_parse_issue_comment_non_pr(gitea_adapter):
    """Verify comments on regular issues (not PRs) are ignored."""
    headers = {"X-Gitea-Event": "issue_comment"}
    payload = {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "sender": {"username": "developer_alice"},
        "issue": {"number": 10},  # No pull_request key
        "comment": {"id": 55, "body": "Standard issue comment"},
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is None


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_comments(gitea_adapter):
    """Verify fetching and parsing PR comments with bot identification."""
    respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/issues/42/comments"
    ).mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "user": {"username": "developer_alice"},
                    "body": "Can you check line 10?",
                    "created_at": "2026-09-19T10:00:00Z",
                },
                {
                    "id": 2,
                    "user": {"username": "git_bot"},
                    "body": (
                        "<!-- git-bot-comment -->\n\n🤖 **git_bot**\n\nLooks good."
                    ),
                    "created_at": "2026-09-19T10:05:00Z",
                },
            ],
        )
    )
    respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/pulls/42/reviews"
    ).mock(
        return_value=Response(
            200,
            json=[{"id": 10}],
        )
    )
    respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/pulls/42/reviews/10/comments"
    ).mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 3,
                    "user": {"username": "other_reviewer"},
                    "body": "Nit: naming could be clearer.",
                    "created_at": "2026-09-19T10:10:00Z",
                    "path": "src/app.py",
                    "line_num": 25,
                }
            ],
        )
    )

    comments = await gitea_adapter.get_pr_comments("owner/repo", 42)
    assert len(comments) == 3
    assert comments[0].author == "developer_alice"
    assert comments[0].is_bot is False

    # Second comment is from gitbert
    assert comments[1].author == "git_bot"
    assert comments[1].is_bot is True

    # Third comment is inline review comment
    assert comments[2].author == "other_reviewer"
    assert comments[2].path == "src/app.py"
    assert comments[2].line == 25


@pytest.mark.asyncio
@respx.mock
async def test_post_pr_comment(gitea_adapter):
    """Verify posting PR comment includes watermark and bot branding."""
    comment_mock = respx.post(
        "https://gitea.example.com/api/v1/repos/owner/repo/issues/42/comments"
    ).mock(return_value=Response(201, json={"id": 123}))

    await gitea_adapter.post_pr_comment(
        "owner/repo", 42, "Here is how to optimize the database query."
    )
    assert comment_mock.called
    body = comment_mock.calls.last.request.read().decode("utf-8")
    assert "<!-- gitbert-comment -->" in body
    assert "🤖 **Gitbert**" in body
    assert "Here is how to optimize the database query." in body


def test_parse_status_event(gitea_adapter):
    """Verify parsing Gitea commit status event."""
    headers = {"X-Gitea-Event": "status"}
    payload = {
        "sha": "head-sha-777",
        "state": "failure",
        "context": "ci/test",
        "description": "Tests failed: 1 failure",
        "target_url": "https://gitea.example.com/owner/repo/actions/runs/12",
        "repository": {"full_name": "owner/repo"},
        "sender": {"username": "gitea_actions"},
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is not None
    assert event.event_type == EventType.STATUS
    assert event.head_sha == "head-sha-777"
    assert event.status_state == "failure"
    assert event.status_context == "ci/test"
    assert "actions/runs/12" in event.target_url


def test_parse_status_event_loop_prevention(gitea_adapter):
    """Verify bot's own commit statuses are ignored to prevent loops."""
    headers = {"X-Gitea-Event": "status"}

    # Modern gitbert context
    payload = {
        "sha": "head-sha-777",
        "state": "success",
        "context": "gitbert/pr-review",
        "repository": {"full_name": "owner/repo"},
        "sender": {"username": "git_bot"},
    }
    event = gitea_adapter.parse_event(headers, payload)
    assert event is not None
    assert event.event_type == EventType.IGNORED

    # Legacy git-bot context
    legacy_payload = {
        "sha": "head-sha-777",
        "state": "success",
        "context": "git-bot/pr-review",
        "repository": {"full_name": "owner/repo"},
        "sender": {"username": "git_bot"},
    }
    legacy_event = gitea_adapter.parse_event(headers, legacy_payload)
    assert legacy_event is not None
    assert legacy_event.event_type == EventType.IGNORED


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_statuses(gitea_adapter):
    """Verify fetching commit statuses from Gitea."""
    respx.get(
        "https://gitea.example.com/api/v1/repos/owner/repo/commits/abc123/statuses"
    ).mock(
        return_value=Response(
            200,
            json=[
                {
                    "status": "pending",
                    "context": "ci/build",
                    "description": "Building...",
                },
                {
                    "status": "success",
                    "context": "ci/lint",
                    "description": "Lint passed",
                },
            ],
        )
    )

    statuses = await gitea_adapter.get_commit_statuses("owner/repo", "abc123")
    assert len(statuses) == 2
    assert statuses[0].context == "ci/build"
    assert statuses[0].state == CommitState.PENDING
    assert statuses[1].context == "ci/lint"
    assert statuses[1].state == CommitState.SUCCESS


@pytest.mark.asyncio
@respx.mock
async def test_get_action_log(gitea_adapter):
    """Verify fetching Action logs from URL."""
    respx.get("https://gitea.example.com/logs/job-1").mock(
        return_value=Response(
            200,
            text=(
                "Traceback (most recent call last):\n"
                "  File 'app.py', line 10, in <module>\n"
                "ValueError: invalid config\n"
            ),
        )
    )

    log = await gitea_adapter.get_action_log(
        "owner/repo", "https://gitea.example.com/logs/job-1"
    )
    assert "ValueError: invalid config" in log


@pytest.mark.asyncio
@respx.mock
async def test_get_action_log_relative_url(gitea_adapter):
    """Verify relative log URLs are resolved against Gitea base_url."""
    respx.get("https://gitea.example.com/owner/repo/actions/1/logs").mock(
        return_value=Response(200, text="Build passed successfully.")
    )
    log = await gitea_adapter.get_action_log("owner/repo", "/owner/repo/actions/1/logs")
    assert "Build passed successfully." in log


@pytest.mark.asyncio
async def test_get_action_log_ssrf_blocked_metadata(gitea_adapter):
    """Verify SSRF attempt to AWS metadata endpoint is blocked."""
    log = await gitea_adapter.get_action_log(
        "owner/repo", "http://169.254.169.254/latest/meta-data/"
    )
    assert "Security Error:" in log
    assert "forbidden" in log.lower() or "restricted" in log.lower()


@pytest.mark.asyncio
async def test_get_action_log_ssrf_blocked_private_ips(gitea_adapter):
    """Verify SSRF attempts to private networks are blocked."""
    for ip in [
        "http://10.0.0.1/admin",
        "http://192.168.1.1/internal",
        "http://127.0.0.1:8080/",
    ]:
        log = await gitea_adapter.get_action_log("owner/repo", ip)
        assert "Security Error:" in log, f"Expected {ip} to be blocked"


@pytest.mark.asyncio
@respx.mock
async def test_get_action_log_external_safe_url_strips_auth_token(gitea_adapter):
    """Verify external logs are fetched without sending Gitea credentials."""
    route = respx.get("https://public-ci.example.org/job-42.log").mock(
        return_value=Response(200, text="Remote external log content.")
    )
    log = await gitea_adapter.get_action_log(
        "owner/repo", "https://public-ci.example.org/job-42.log"
    )
    assert "Remote external log content." in log
    # Verify Gitea token was NOT sent to the external host
    assert route.called
    request = route.calls.last.request
    assert "Authorization" not in request.headers


@pytest.mark.asyncio
async def test_gitea_adapter_connection_reuse_and_aclose():
    """Verify client reuse across calls and clean shutdown with aclose."""
    adapter = GiteaAdapter(base_url="https://gitea.example.com")
    client1 = adapter._get_client()
    client2 = adapter._get_client()
    assert client1 is client2  # Connection pooling / client reuse
    assert not client1.is_closed
    await adapter.aclose()
    assert client1.is_closed


@pytest.mark.asyncio
@respx.mock
async def test_find_pr_for_commit(gitea_adapter):
    """Verify finding open PR matching commit head SHA."""
    respx.get("https://gitea.example.com/api/v1/repos/owner/repo/pulls").mock(
        return_value=Response(
            200,
            json=[
                {"number": 10, "head": {"sha": "other-sha"}},
                {"number": 15, "head": {"sha": "target-sha"}},
            ],
        )
    )

    pr_num = await gitea_adapter.find_pr_for_commit("owner/repo", "target-sha")
    assert pr_num == 15

    not_found = await gitea_adapter.find_pr_for_commit("owner/repo", "missing-sha")
    assert not_found is None
