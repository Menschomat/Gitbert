"""Factory for constructing isolated, scoped ADK reviewer agent."""

from typing import Any

from google.adk.agents.llm_agent import Agent

from gitbert.agent.prompts import REVIEWER_SYSTEM_INSTRUCTION
from gitbert.config import get_settings
from gitbert.platforms.base import ICodePlatform
from gitbert.security.context import ScopedMRContext
from gitbert.tools.scoped_review_tools import create_scoped_tools


def build_reviewer_agent(
    context: ScopedMRContext,
    platform: ICodePlatform,
    model_override: Any | None = None,
) -> Agent:
    """Construct an ADK Agent configured strictly for this MR session.

    Args:
        context: ScopedMRContext locking the session to a specific MR.
        platform: Platform adapter.
        model_override: Optional override for model name or LiteLlm instance.

    Returns:
        Configured ADK Agent.
    """
    settings = get_settings()
    tools = create_scoped_tools(context, platform)
    selected_model = model_override or settings.get_adk_model()
    agent_desc = f"Code Reviewer for {context.repo} MR #{context.pr_number}"

    return Agent(
        name=f"pr_reviewer_{context.pr_number}",
        model=selected_model,
        description=agent_desc,
        instruction=REVIEWER_SYSTEM_INSTRUCTION,
        tools=tools,
    )
