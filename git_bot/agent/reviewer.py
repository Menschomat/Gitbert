"""Factory for constructing isolated, scoped ADK reviewer agent."""

from google.adk.agents.llm_agent import Agent

from git_bot.agent.prompts import REVIEWER_SYSTEM_INSTRUCTION
from git_bot.config import get_settings
from git_bot.platforms.base import ICodePlatform
from git_bot.security.context import ScopedMRContext
from git_bot.tools.scoped_review_tools import create_scoped_tools


def build_reviewer_agent(
    context: ScopedMRContext,
    platform: ICodePlatform,
    model_name: str | None = None,
) -> Agent:
    """Construct an ADK Agent configured strictly for this MR session.

    Args:
        context: ScopedMRContext locking the session to a specific MR.
        platform: Platform adapter.
        model_name: Optional override for Gemini model name.

    Returns:
        Configured ADK Agent.
    """
    settings = get_settings()
    tools = create_scoped_tools(context, platform)
    selected_model = model_name or settings.model_name
    agent_desc = f"Code Reviewer for {context.repo} MR #{context.pr_number}"

    return Agent(
        name=f"pr_reviewer_{context.pr_number}",
        model=selected_model,
        description=agent_desc,
        instruction=REVIEWER_SYSTEM_INSTRUCTION,
        tools=tools,
    )
