"""Tests for agent configuration and registration."""

from unittest.mock import AsyncMock

from gitbert.agent import build_reviewer_agent
from gitbert.platforms.base import ICodePlatform
from gitbert.security.context import ScopedMRContext


def test_agent_package_exports():
    """Verify agent package exports build_reviewer_agent correctly."""
    assert callable(build_reviewer_agent)


def test_reviewer_agent_configuration():
    """Verify build_reviewer_agent sets up proper instructions and name."""
    mock_platform = AsyncMock(spec=ICodePlatform)
    context = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=42,
        head_sha="head-123",
        base_sha="base-456",
        allowed_files=frozenset(["src/app.py"]),
    )
    agent = build_reviewer_agent(context, mock_platform)
    assert agent.name == "pr_reviewer_42"
    assert "Gitbert" in agent.instruction
    assert "senior developer" in agent.instruction.lower()
