"""Tests for security scoping and context guardrails."""

import pytest

from git_bot.security.context import ScopedMRContext
from git_bot.security.exceptions import SecurityScopeViolationError


def test_scoped_context_immutability():
    """Verify ScopedMRContext is frozen and cannot be modified at runtime."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py", "README.md"]),
    )
    with pytest.raises((AttributeError, TypeError)):
        ctx.repo = "other/repo"  # type: ignore[misc]


def test_allowed_file_access():
    """Verify files in the MR allowlist pass validation."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py", "docs/index.md"]),
    )
    assert ctx.validate_file_access("src/main.py") == "src/main.py"
    # Leading ./ should be normalized
    assert ctx.validate_file_access("./docs/index.md") == "docs/index.md"


def test_unauthorized_file_raises_violation():
    """Verify accessing files outside the MR diff raises SecurityScopeViolationError."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py"]),
    )
    with pytest.raises(SecurityScopeViolationError) as excinfo:
        ctx.validate_file_access(".env")
    assert "Access Denied" in str(excinfo.value)
    assert ".env" in str(excinfo.value)


def test_path_traversal_attack_blocked():
    """Verify path traversal attacks like ../../etc/passwd are blocked."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py"]),
    )
    with pytest.raises(SecurityScopeViolationError):
        ctx.validate_file_access("../../etc/passwd")

    with pytest.raises(SecurityScopeViolationError):
        ctx.validate_file_access("/etc/shadow")
