"""Abstract interface for code hosting platforms (Gitea, GitHub, GitLab)."""

from abc import ABC, abstractmethod
from typing import Any

from gitbert.models.comments import PRComment
from gitbert.models.events import PRReviewEvent
from gitbert.models.platform import CommitStatus, PRMetadata
from gitbert.models.review import ReviewResult


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

    @abstractmethod
    async def get_pr_comments(self, repo: str, pr_number: int) -> list[PRComment]:
        """Fetch discussion comments and review comments on the pull request."""
        pass

    @abstractmethod
    async def post_pr_comment(self, repo: str, pr_number: int, body: str) -> None:
        """Post a comment to the pull request discussion thread."""
        pass

    @abstractmethod
    async def get_commit_statuses(self, repo: str, sha: str) -> list[CommitStatus]:
        """Fetch all commit status checks for a given commit hash."""
        pass

    @abstractmethod
    async def get_action_log(self, repo: str, target_url: str | None = None) -> str:
        """Fetch failure logs of a CI Action job."""
        pass

    @abstractmethod
    async def find_pr_for_commit(self, repo: str, sha: str) -> int | None:
        """Find the open pull request number associated with a commit SHA."""
        pass
