"""Abstract interface for code hosting platforms (Gitea, GitHub, GitLab)."""

from abc import ABC, abstractmethod
from typing import Any

from git_bot.models.events import PRReviewEvent
from git_bot.models.platform import CommitStatus, PRMetadata
from git_bot.models.review import ReviewResult


class ICodePlatform(ABC):
    """Abstract code platform adapter."""

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        """Verify webhook signature or secret token."""
        pass

    @abstractmethod
    def parse_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> PRReviewEvent | None:
        """Parse raw webhook headers and payload into normalized domain event."""
        pass

    @abstractmethod
    async def get_pr_metadata(self, repo: str, pr_number: int) -> PRMetadata:
        """Fetch metadata and changed files for a pull request."""
        pass

    @abstractmethod
    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch unified diff for the full pull request."""
        pass

    @abstractmethod
    async def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """Fetch raw content of a specific file."""
        pass

    @abstractmethod
    async def submit_review(
        self, repo: str, pr_number: int, review: ReviewResult
    ) -> None:
        """Post review summary and inline comments to the pull request."""
        pass

    @abstractmethod
    async def set_commit_status(
        self, repo: str, sha: str, status: CommitStatus
    ) -> None:
        """Set commit status check (e.g. pending, success, failure)."""
        pass

    @abstractmethod
    async def list_directory(
        self, repo: str, path: str = "", ref: str = ""
    ) -> list[dict[str, Any]]:
        """List files and subdirectories at a given repository path."""
        pass
