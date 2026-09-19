"""Configuration settings for git_bot."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env variables if present
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Agent runtime settings."""

    model: str = os.getenv("MODEL_NAME", "gemini-2.0-flash")
    agent_name: str = os.getenv("AGENT_NAME", "git_bot")
    agent_description: str = os.getenv(
        "AGENT_DESCRIPTION",
        "A base AI agent ready for extensions, currently equipped with time tools.",
    )
    instruction: str = os.getenv(
        "AGENT_INSTRUCTION",
        (
            "You are an AI assistant. You can check the current date and time "
            "using your time tool when requested. Be concise, accurate, and helpful."
        ),
    )


settings = Settings()
