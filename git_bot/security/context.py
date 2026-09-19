"""Per-invocation scoped MR context enforcing hard security boundaries."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from git_bot.security.exceptions import SecurityScopeViolationError


@dataclass(frozen=True)
class ScopedMRContext:
    """Immutable context locking an agent review session to a specific MR."""

    platform: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    allowed_files: frozenset[str]

    def validate_file_access(self, file_path: str) -> str:
        """Validate that a file path is permitted within this MR's scope.

        Args:
            file_path: Relative path of the requested file.

        Returns:
            Normalized relative path.

        Raises:
            SecurityScopeViolationError: If the file path is invalid, traverses
                                         directories, or is not in the allowlist.
        """
        # Block absolute paths
        if file_path.startswith("/"):
            raise SecurityScopeViolationError(
                f"Access Denied: Absolute path '{file_path}' is not permitted."
            )

        # Normalize path
        normalized = PurePosixPath(file_path).as_posix()
        if normalized.startswith("./"):
            normalized = normalized[2:]

        # Block path traversal attempts
        if ".." in normalized.split("/"):
            raise SecurityScopeViolationError(
                f"Access Denied: Directory traversal in '{file_path}' is forbidden."
            )

        # Check against MR diff allowlist
        if normalized not in self.allowed_files:
            file_count = len(self.allowed_files)
            raise SecurityScopeViolationError(
                f"Access Denied: File '{file_path}' is outside Merge Request "
                f"#{self.pr_number} allowlist ({file_count} files allowed)."
            )

        return normalized

    @property
    def session_key(self) -> str:
        """Generate a globally unique, collision-proof ADK session ID."""
        return f"{self.platform}:{self.repo}:{self.pr_number}:{self.head_sha}"
