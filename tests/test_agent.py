"""Tests for agent configuration and registration."""

from git_bot.agent import root_agent
from git_bot.tools import get_current_time


def test_agent_initialization():
    """Verify root_agent is configured properly with instructions and model."""
    assert root_agent is not None
    assert root_agent.name == "git_bot"
    assert "time" in root_agent.instruction.lower()


def test_agent_tools_registered():
    """Verify time tool is registered in root_agent."""
    assert root_agent.tools is not None
    assert len(root_agent.tools) == 1
    assert root_agent.tools[0] == get_current_time
