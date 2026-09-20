"""Agent package."""

from google.adk.agents.llm_agent import Agent

from gitbert.agent.reviewer import build_reviewer_agent
from gitbert.config import settings
from gitbert.tools import get_current_time

# Root baseline agent for ADK CLI and general inspection
root_agent = Agent(
    name=settings.agent_name,
    model=settings.model,
    description=settings.agent_description,
    instruction=settings.instruction,
    tools=[get_current_time],
)

__all__ = ["build_reviewer_agent", "root_agent"]
