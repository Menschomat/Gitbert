"""Gitea platform adapter implementation using httpx."""

import hashlib
import hmac
from typing import Any

import httpx
from pydantic import SecretStr

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
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.webhook_secret = webhook_secret
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
        if event_name != "pull_request":
            return None

        action = payload.get("action")
        repo_data = payload.get("repository", {})
        repo_full_name = repo_data.get("full_name", "")
        pr_number = payload.get("number", 0)
        pr_data = payload.get("pull_request", {})
        sender_data = payload.get("sender", {})
        sender = sender_data.get("username", "unknown")

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
