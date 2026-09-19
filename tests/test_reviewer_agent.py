"""Tests for ADK reviewer agent instantiation."""

from unittest.mock import AsyncMock

from git_bot.agent.reviewer import build_reviewer_agent
from git_bot.platforms.base import ICodePlatform
from git_bot.security.context import ScopedMRContext


def test_build_reviewer_agent():
    """Verify reviewer agent is constructed with scoped tools and instructions."""
    mock_platform = AsyncMock(spec=ICodePlatform)
    context = ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=5,
        head_sha="head-123",
        base_sha="base-456",
        allowed_files=frozenset(["src/app.py"]),
    )

    agent = build_reviewer_agent(context, mock_platform)
    assert agent is not None
    assert "review" in agent.name.lower()
    assert agent.tools is not None
    assert len(agent.tools) == 3
    tool_names = [t.__name__ for t in agent.tools]
    assert "get_pr_diff" in tool_names
    assert "get_file_content" in tool_names
    assert "get_pr_metadata" in tool_names
