"""Main agent definition for git_bot using Google ADK 2.0."""

from google.adk.agents.llm_agent import Agent

from git_bot.config import settings
from git_bot.tools import get_current_time

# Root agent definition required by ADK CLI and runners
root_agent = Agent(
    name=settings.agent_name,
    model=settings.model,
    description=settings.agent_description,
    instruction=settings.instruction,
    tools=[get_current_time],
)
