"""Gitea platform adapter implementation using httpx."""

import hashlib
import hmac
import ipaddress
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from gitbert.models.comments import CommentType, PRComment
from gitbert.models.events import EventType, PRReviewEvent
from gitbert.models.platform import ChangedFile, CommitState, CommitStatus, PRMetadata
from gitbert.models.review import ReviewResult
from gitbert.platforms.base import ICodePlatform


def is_safe_external_url(url: str) -> tuple[bool, str]:
    """Validate an external URL against SSRF and private network attacks."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'."

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname in target URL."

    lower_host = hostname.lower()
    if (
        lower_host in ("localhost", "127.0.0.1", "::1")
        or lower_host.endswith(".local")
        or lower_host.endswith(".internal")
        or lower_host.endswith(".localhost")
    ):
        return False, f"Access to local hostname '{hostname}' is forbidden."

    # Direct IP validation
    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return (
                False,
                f"Access to private or restricted IP '{hostname}' is forbidden.",
            )
        return True, ""
    except ValueError:
        pass

    # Resolve domain to IP addresses to prevent DNS rebinding attacks
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for *_, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                msg = (
                    f"Resolved IP '{ip_str}' for '{hostname}' is in a restricted range."
                )
                return False, msg
    except (socket.gaierror, socket.herror):
        # Hostname could not be resolved (e.g. mock domain in test, or DNS failure)
        pass
    except Exception as exc:
        return False, f"DNS resolution failed for '{hostname}': {exc}"

    return True, ""


class GiteaAdapter(ICodePlatform):
    """Gitea REST API and webhook adapter."""

    def __init__(
        self,
        base_url: str,
        token: SecretStr | None = None,
        webhook_secret: SecretStr | None = None,
        bot_name: str = "Gitbert",
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.webhook_secret = webhook_secret
        self.bot_name = bot_name
        self._custom_client = client
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Create or return a reusable async HTTP client with connection pooling."""
        if self._custom_client is not None:
            return self._custom_client

        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/json",
                "User-Agent": "Gitbert-Reviewer/1.0",
            }
            if self.token:
                headers["Authorization"] = f"token {self.token.get_secret_value()}"

            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def aclose(self) -> None:
        """Close managed HTTP client if open."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

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
        if sender.lower() in (self.bot_name.lower(), "gitbert", "git_bot"):
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

        # 4. Commit Status events (from CI / Gitea Actions)
        if event_name == "status":
            context = payload.get("context", "")
            # Loop protection: Ignore status events published by Gitbert itself
            if context.startswith(("gitbert/", "git-bot/")):
                return PRReviewEvent(
                    event_type=EventType.IGNORED,
                    platform="gitea",
                    repo=repo_full_name,
                    sender=sender,
                )

            sha = payload.get("sha", "")
            state = payload.get("state", "")
            description = payload.get("description", "")
            target_url = payload.get("target_url", "")

            return PRReviewEvent(
                event_type=EventType.STATUS,
                platform="gitea",
                repo=repo_full_name,
                sender=sender,
                head_sha=sha,
                status_state=state,
                status_context=context,
                target_url=target_url,
                status_description=description,
                raw_payload=payload,
            )

        return None

    async def get_pr_metadata(self, repo: str, pr_number: int) -> PRMetadata:
        """Fetch metadata and changed files for a pull request."""
        client = self._get_client()
        # 1. Fetch PR details
        pr_resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_number}")
        pr_resp.raise_for_status()
        pr_json = pr_resp.json()

        # 2. Fetch changed files list
        files_resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_number}/files")
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
        client = self._get_client()
        resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_number}.diff")
        resp.raise_for_status()
        return resp.text

    async def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """Fetch raw content of a specific file."""
        client = self._get_client()
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
        client = self._get_client()
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
        client = self._get_client()
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

        client = self._get_client()
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
        client = self._get_client()

        # 1. Issue/PR discussion comments
        try:
            resp = await client.get(f"/api/v1/repos/{repo}/issues/{pr_number}/comments")
            if resp.status_code == 200:
                for item in resp.json():
                    author = item.get("user", {}).get("username", "")
                    body = item.get("body", "")
                    is_bot = (
                        author.lower() == self.bot_name.lower()
                        or "<!-- gitbert" in body
                        or "<!-- git-bot" in body
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
                                or "<!-- gitbert" in body
                                or "<!-- git-bot" in body
                            )
                            dt_str = item.get("created_at")
                            created_at = (
                                datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
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
        watermarked = f"<!-- gitbert-comment -->\n\n🤖 **{self.bot_name}**\n\n{body}"
        client = self._get_client()
        resp = await client.post(
            f"/api/v1/repos/{repo}/issues/{pr_number}/comments",
            json={"body": watermarked},
        )
        resp.raise_for_status()

    async def get_commit_statuses(self, repo: str, sha: str) -> list[CommitStatus]:
        """Fetch all commit status checks for a given commit hash."""
        url = f"/api/v1/repos/{repo}/commits/{sha}/statuses"
        statuses: list[CommitStatus] = []
        client = self._get_client()
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        raw_state = item.get("status", item.get("state", "pending"))
                        try:
                            state = CommitState(raw_state.lower())
                        except ValueError:
                            state = CommitState.PENDING
                        statuses.append(
                            CommitStatus(
                                state=state,
                                description=item.get("description", ""),
                                context=item.get("context", "default"),
                                target_url=item.get("target_url"),
                            )
                        )
        except httpx.HTTPError:
            pass
        return statuses

    async def get_action_log(self, repo: str, target_url: str | None = None) -> str:
        """Fetch failure logs of a CI Action job safely with SSRF protection."""
        if not target_url:
            return "No log URL provided."

        target_url = target_url.strip()
        parsed_target = urlparse(target_url)
        parsed_base = urlparse(self.base_url)

        # 1. Check if relative URL or belongs strictly to the configured Gitea base_url
        is_same_origin = not parsed_target.netloc or (
            parsed_target.scheme.lower() == parsed_base.scheme.lower()
            and parsed_target.netloc.lower() == parsed_base.netloc.lower()
        )

        if is_same_origin:
            # Internal request to Gitea: Safe to use authenticated client
            client = self._get_client()
            try:
                resp = await client.get(target_url)
                if resp.status_code == 200:
                    return resp.text
                return f"Failed to fetch logs: HTTP {resp.status_code}"
            except Exception as exc:
                return f"Error retrieving logs from {target_url}: {exc}"

        # 2. External URL: Validate against SSRF and private networks
        is_safe, error_msg = is_safe_external_url(target_url)
        if not is_safe:
            return f"Security Error: {error_msg}"

        # 3. External URL: Use unauthenticated client (never leak credentials)
        try:
            async with httpx.AsyncClient(timeout=15.0) as unauth_client:
                resp = await unauth_client.get(target_url)
                if resp.status_code == 200:
                    return resp.text
                return f"Failed to fetch logs: HTTP {resp.status_code}"
        except Exception as exc:
            return f"Error retrieving logs from external URL {target_url}: {exc}"

    async def find_pr_for_commit(self, repo: str, sha: str) -> int | None:
        """Find the open pull request number associated with a commit SHA."""
        client = self._get_client()
        try:
            resp = await client.get(
                f"/api/v1/repos/{repo}/pulls",
                params={"state": "open", "limit": 50},
            )
            if resp.status_code == 200:
                pulls = resp.json()
                if isinstance(pulls, list):
                    for pr in pulls:
                        head_sha = pr.get("head", {}).get("sha", "")
                        if head_sha == sha:
                            return pr.get("number")
        except httpx.HTTPError:
            pass
        return None
