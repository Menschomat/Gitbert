"""Per-invocation scoped MR context enforcing hard security boundaries."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from git_bot.security.exceptions import SecurityScopeViolationError

# Patterns and extensions strictly blocked from LLM inspection to prevent secret leakage
SENSITIVE_NAME_PATTERNS = (
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "credentials.json",
    "secret.json",
    "secrets.json",
)

SENSITIVE_EXTENSIONS = (
    ".pem",
    ".key",
    ".pkcs12",
    ".pfx",
    ".p12",
    ".kdbx",
)


@dataclass(frozen=True)
class ScopedMRContext:
    """Immutable context locking an agent review session to a specific MR.

    Allows reading repository codebase files on the MR branch for deep architectural
    context, while strictly blocking directory traversal, other repositories,
    and sensitive secret files.
    """

    platform: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    allowed_files: frozenset[str]  # Files modified in this specific MR

    def validate_file_access(self, file_path: str) -> str:
        """Validate that a file path is permitted within this repository's scope.

        Args:
            file_path: Relative path of the requested file.

        Returns:
            Normalized relative path.

        Raises:
            SecurityScopeViolationError: If the path is invalid, traverses
                                         directories, or matches sensitive file policy.
        """
        # 1. Block absolute paths
        if file_path.startswith("/"):
            raise SecurityScopeViolationError(
                f"Access Denied: Absolute path '{file_path}' is not permitted."
            )

        # 2. Normalize path
        normalized = PurePosixPath(file_path).as_posix()
        if normalized.startswith("./"):
            normalized = normalized[2:]

        parts = normalized.split("/")

        # 3. Block path traversal attempts
        if ".." in parts:
            raise SecurityScopeViolationError(
                f"Access Denied: Directory traversal in '{file_path}' is forbidden."
            )

        # 4. Block .git internal metadata
        if ".git" in parts:
            raise SecurityScopeViolationError(
                f"Access Denied: VCS metadata in '{file_path}' is forbidden."
            )

        file_name = parts[-1].lower()

        # 5. Block environment secret files (.env, .env.local, etc.)
        if file_name == ".env" or file_name.startswith(".env."):
            raise SecurityScopeViolationError(
                f"Access Denied: '{file_path}' is restricted by sensitive file policy."
            )

        # 6. Block private keys and credential files
        if any(pattern in file_name for pattern in SENSITIVE_NAME_PATTERNS):
            raise SecurityScopeViolationError(
                f"Access Denied: Key or credential file '{file_path}' is restricted."
            )

        # 7. Block sensitive certificates and keystores
        if any(file_name.endswith(ext) for ext in SENSITIVE_EXTENSIONS):
            raise SecurityScopeViolationError(
                f"Access Denied: Sensitive extension in '{file_path}' is restricted."
            )

        return normalized

    def is_modified_in_mr(self, file_path: str) -> bool:
        """Check if a file was modified directly in this MR."""
        try:
            normalized = self.validate_file_access(file_path)
            return normalized in self.allowed_files
        except SecurityScopeViolationError:
            return False

    @property
    def session_key(self) -> str:
        """Generate a globally unique, collision-proof ADK session ID."""
        return f"{self.platform}:{self.repo}:{self.pr_number}:{self.head_sha}"
