"""Gitea platform adapter implementation using httpx."""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from git_bot.models.comments import CommentType, PRComment
from git_bot.models.events import EventType, PRReviewEvent
from git_bot.models.platform import ChangedFile, CommitStatus, PRMetadata
from git_bot.models.review import ReviewResult
from git_bot.platforms.base import ICodePlatform


class GiteaAdapter(ICodePlatform):
    """Gitea REST API and webhook adapter."""

    def __init__(
        self,
        base_url: str,
        token: SecretStr | None = None,
        webhook_secret: SecretStr | None = None,
        bot_name: str = "git_bot",
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.webhook_secret = webhook_secret
        self.bot_name = bot_name
        self._custom_client = client

    def _get_client(self) -> httpx.AsyncClient:
        """Create or return an async HTTP client."""
        if self._custom_client is not None:
            return self._custom_client

        headers = {
            "Accept": "application/json",
            "User-Agent": "git_bot-Reviewer/1.0",
        }
        if self.token:
            headers["Authorization"] = f"token {self.token.get_secret_value()}"

        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
        )

    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        """Verify HMAC-SHA256 signature from X-Gitea-Signature header."""
        if not self.webhook_secret:
            # If no secret is configured, consider it unverified unless in test mode
            return False

        signature = headers.get("X-Gitea-Signature") or headers.get("x-gitea-signature")
        if not signature:
            return False

        secret_bytes = self.webhook_secret.get_secret_value().encode("utf-8")
        computed = hmac.new(secret_bytes, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, computed)

    def parse_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> PRReviewEvent | None:
        """Parse Gitea webhook event into normalized domain event."""
        event_name = headers.get("X-Gitea-Event") or headers.get("x-gitea-event")
        if not event_name:
            return None

        repo_data = payload.get("repository", {})
        repo_full_name = repo_data.get("full_name", "")
        sender_data = payload.get("sender", {})
        sender = sender_data.get("username", "unknown")
        action = payload.get("action")

        # 1. Infinite Loop Prevention: Never respond to bot's own events
        if sender.lower() == self.bot_name.lower():
            return PRReviewEvent(
                event_type=EventType.IGNORED,
                platform="gitea",
                repo=repo_full_name,
                pr_number=payload.get("number", 0),
                sender=sender,
            )

        # 2. Pull Request lifecycle events
        if event_name == "pull_request":
            pr_number = payload.get("number", 0)
            pr_data = payload.get("pull_request", {})
            head = pr_data.get("head", {})
            base = pr_data.get("base", {})
            head_sha = head.get("sha", "")
            base_sha = base.get("sha", "")
            is_draft = pr_data.get("draft", False)

            if action in ("opened", "reopened"):
                event_type = EventType.PR_OPENED
            elif action == "synchronized":
                event_type = EventType.PR_UPDATED
            else:
                event_type = EventType.IGNORED

            return PRReviewEvent(
                event_type=event_type,
                platform="gitea",
                repo=repo_full_name,
                pr_number=pr_number,
                sender=sender,
                head_sha=head_sha,
                base_sha=base_sha,
                is_draft=is_draft,
                raw_payload=payload,
            )

        # 3. Comment events (discussion comment on PR or inline review comment)
        if event_name == "issue_comment":
            issue = payload.get("issue", {})
            # Only process if this issue is actually a Pull Request
            if not issue.get("pull_request"):
                return None

            if action != "created":
                return PRReviewEvent(
                    event_type=EventType.IGNORED,
                    platform="gitea",
                    repo=repo_full_name,
                    pr_number=issue.get("number", 0),
                    sender=sender,
                )

            pr_number = issue.get("number", 0)
            comment_data = payload.get("comment", {})
            comment_id = comment_data.get("id")
            comment_body = comment_data.get("body", "")

            return PRReviewEvent(
                event_type=EventType.COMMENT,
                platform="gitea",
                repo=repo_full_name,
                pr_number=pr_number,
                sender=sender,
                comment_id=comment_id,
                comment_body=comment_body,
                raw_payload=payload,
            )

        if event_name in ("pull_request_comment", "pull_request_review_comment"):
            if action != "created":
                return PRReviewEvent(
                    event_type=EventType.IGNORED,
                    platform="gitea",
                    repo=repo_full_name,
                    pr_number=payload.get("pull_request", {}).get("number", 0),
                    sender=sender,
                )

            pr_data = payload.get("pull_request", {})
            pr_number = pr_data.get("number", 0)
            comment_data = payload.get("comment", {})
            comment_id = comment_data.get("id")
            comment_body = comment_data.get("body", "")

            return PRReviewEvent(
                event_type=EventType.COMMENT,
                platform="gitea",
                repo=repo_full_name,
                pr_number=pr_number,
                sender=sender,
                comment_id=comment_id,
                comment_body=comment_body,
                raw_payload=payload,
            )

        return None

    async def get_pr_metadata(self, repo: str, pr_number: int) -> PRMetadata:
        """Fetch metadata and changed files for a pull request."""
        async with self._get_client() as client:
            # 1. Fetch PR details
            pr_resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_number}")
            pr_resp.raise_for_status()
            pr_json = pr_resp.json()

            # 2. Fetch changed files list
            files_resp = await client.get(
                f"/api/v1/repos/{repo}/pulls/{pr_number}/files"
            )
            files_resp.raise_for_status()
            files_json = files_resp.json()

            changed_files = [
                ChangedFile(
                    filename=f.get("filename", ""),
                    status=f.get("status", "modified"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                )
                for f in files_json
            ]

            base = pr_json.get("base", {})
            head = pr_json.get("head", {})

            return PRMetadata(
                repo=repo,
                number=pr_number,
                title=pr_json.get("title", ""),
                body=pr_json.get("body", "") or "",
                author=pr_json.get("user", {}).get("username", "unknown"),
                base_ref=base.get("ref", ""),
                base_sha=base.get("sha", ""),
                head_ref=head.get("ref", ""),
                head_sha=head.get("sha", ""),
                is_draft=pr_json.get("draft", False),
                changed_files=changed_files,
            )

    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch full unified diff for the PR."""
        async with self._get_client() as client:
            resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_number}.diff")
            resp.raise_for_status()
            return resp.text

    async def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """Fetch raw content of a specific file."""
        async with self._get_client() as client:
            resp = await client.get(f"/api/v1/repos/{repo}/raw/{path}?ref={ref}")
            resp.raise_for_status()
            return resp.text

    async def submit_review(
        self, repo: str, pr_number: int, review: ReviewResult
    ) -> None:
        """Submit review verdict and comments to Gitea."""
        body = {
            "event": review.decision.value,
            "body": review.summary,
            "comments": [
                {
                    "path": c.path,
                    "new_position": c.new_position,
                    "body": c.body,
                }
                for c in review.inline_comments
            ],
        }
        async with self._get_client() as client:
            resp = await client.post(
                f"/api/v1/repos/{repo}/pulls/{pr_number}/reviews",
                json=body,
            )
            resp.raise_for_status()

    async def set_commit_status(
        self, repo: str, sha: str, status: CommitStatus
    ) -> None:
        """Update commit status check in Gitea."""
        payload = {
            "state": status.state.value,
            "context": status.context,
            "description": status.description,
            "target_url": status.target_url or "",
        }
        async with self._get_client() as client:
            resp = await client.post(
                f"/api/v1/repos/{repo}/statuses/{sha}",
                json=payload,
            )
            resp.raise_for_status()

    async def list_directory(
        self, repo: str, path: str = "", ref: str = ""
    ) -> list[dict[str, Any]]:
        """List files and subdirectories at a given repository path."""
        url = f"/api/v1/repos/{repo}/contents"
        if path:
            url = f"{url}/{path.lstrip('/')}"
        params = {"ref": ref} if ref else {}

        async with self._get_client() as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return [
                    {
                        "name": item.get("name", ""),
                        "path": item.get("path", ""),
                        "type": item.get("type", "file"),
                        "size": item.get("size", 0),
                    }
                    for item in data
                ]
            return []

    async def get_pr_comments(self, repo: str, pr_number: int) -> list[PRComment]:
        """Fetch discussion comments and review comments on the pull request."""
        comments: list[PRComment] = []
        async with self._get_client() as client:
            # 1. Issue/PR discussion comments
            try:
                resp = await client.get(
                    f"/api/v1/repos/{repo}/issues/{pr_number}/comments"
                )
                if resp.status_code == 200:
                    for item in resp.json():
                        author = item.get("user", {}).get("username", "")
                        body = item.get("body", "")
                        is_bot = (
                            author.lower() == self.bot_name.lower()
                            or "<!-- git-bot" in body
                            or "🤖 **git_bot**" in body
                        )
                        dt_str = item.get("created_at")
                        created_at = (
                            datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                            if dt_str
                            else datetime.now(UTC)
                        )
                        comments.append(
                            PRComment(
                                id=item.get("id", 0),
                                author=author,
                                is_bot=is_bot,
                                comment_type=CommentType.ISSUE_COMMENT,
                                body=body,
                                created_at=created_at,
                            )
                        )
            except httpx.HTTPError:
                pass

            # 2. Review comments
            try:
                rev_resp = await client.get(
                    f"/api/v1/repos/{repo}/pulls/{pr_number}/reviews"
                )
                if rev_resp.status_code == 200:
                    for rev in rev_resp.json():
                        rev_id = rev.get("id")
                        c_resp = await client.get(
                            f"/api/v1/repos/{repo}/pulls/{pr_number}/reviews/{rev_id}/comments"
                        )
                        if c_resp.status_code == 200:
                            for item in c_resp.json():
                                author = item.get("user", {}).get("username", "")
                                body = item.get("body", "")
                                is_bot = (
                                    author.lower() == self.bot_name.lower()
                                    or "<!-- git-bot" in body
                                    or "🤖 **git_bot**" in body
                                )
                                dt_str = item.get("created_at")
                                created_at = (
                                    datetime.fromisoformat(
                                        dt_str.replace("Z", "+00:00")
                                    )
                                    if dt_str
                                    else datetime.now(UTC)
                                )
                                line_val = item.get("line_num") or item.get("position")
                                comments.append(
                                    PRComment(
                                        id=item.get("id", 0),
                                        author=author,
                                        is_bot=is_bot,
                                        comment_type=CommentType.REVIEW_COMMENT,
                                        body=body,
                                        created_at=created_at,
                                        path=item.get("path"),
                                        line=line_val,
                                        reply_to_id=item.get("reply_to_id"),
                                    )
                                )
            except httpx.HTTPError:
                pass

        comments.sort(key=lambda c: c.created_at)
        return comments

    async def post_pr_comment(self, repo: str, pr_number: int, body: str) -> None:
        """Post a comment to the pull request discussion thread."""
        watermarked = f"<!-- git-bot-comment -->\n\n🤖 **{self.bot_name}**\n\n{body}"
        async with self._get_client() as client:
            resp = await client.post(
                f"/api/v1/repos/{repo}/issues/{pr_number}/comments",
                json={"body": watermarked},
            )
            resp.raise_for_status()
