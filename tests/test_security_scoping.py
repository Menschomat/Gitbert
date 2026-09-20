"""Tests for security scoping and context guardrails."""

import pytest

from gitbert.security.context import ScopedMRContext
from gitbert.security.exceptions import SecurityScopeViolationError


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


def test_codebase_file_access_permitted():
    """Verify reading repository codebase files for context is permitted."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py"]),
    )
    # Both modified and unmodified repository files should be readable
    assert ctx.validate_file_access("src/main.py") == "src/main.py"
    assert ctx.validate_file_access("pyproject.toml") == "pyproject.toml"
    assert ctx.validate_file_access("./src/utils/helpers.py") == "src/utils/helpers.py"


def test_is_modified_in_mr():
    """Verify distinguishing between changed files and context files."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py"]),
    )
    assert ctx.is_modified_in_mr("src/main.py") is True
    assert ctx.is_modified_in_mr("pyproject.toml") is False


def test_sensitive_files_blocked():
    """Verify sensitive files are blocked by the security policy."""
    ctx = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head123",
        base_sha="base456",
        allowed_files=frozenset(["src/main.py"]),
    )
    sensitive_paths = [
        ".env",
        ".env.local",
        "config/.env.prod",
        "id_rsa",
        "keys/id_ed25519",
        "server.key",
        "cert.pem",
        "credentials.json",
        "config/secrets.json",
        ".git/config",
    ]
    for path in sensitive_paths:
        with pytest.raises(SecurityScopeViolationError):
            ctx.validate_file_access(path)


def test_path_traversal_attack_blocked():
    """Verify directory traversal attacks like ../../etc/passwd are blocked."""
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
