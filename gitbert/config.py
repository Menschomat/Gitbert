"""Tiered multi-source configuration for git_bot.

Hierarchy (highest priority first):
1. Explicit function arguments / CLI arguments
2. Environment variables
3. .env file
4. TOML configuration file (e.g. config.toml)
5. Default values
"""

import argparse
import os
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReviewMode(StrEnum):
    """Review operation mode."""

    ADVISORY = "advisory"
    ENFORCING = "enforcing"


class CommentTriggerMode(StrEnum):
    """Trigger mode for answering PR comments."""

    MENTION_ONLY = "mention_only"  # Option A: only replies if explicitly mentioned
    AUTONOMOUS = "autonomous"  # Option B: default, evaluates any comment


class ModelProvider(StrEnum):
    """LLM provider architecture."""

    GEMINI = "gemini"
    LITELLM = "litellm"


def parse_cli_args(args: list[str] | None = None) -> dict[str, Any]:
    """Parse known CLI arguments for configuration overrides."""
    if args is None:
        return {}
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", type=str)
    parser.add_argument("--port", type=int)
    parser.add_argument("--max-concurrent-reviews", type=int)
    parser.add_argument("--review-mode", type=str)
    parser.add_argument("--comment-trigger-mode", type=str)
    parser.add_argument(
        "--await-actions-completion",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--diagnose-action-failures",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--bot-name", type=str)
    parser.add_argument("--model-provider", type=str)
    parser.add_argument("--model-name", type=str)
    parser.add_argument("--openai-compatible-api-key", type=str)
    parser.add_argument("--openai-compatible-model", type=str)
    parser.add_argument("--openai-compatible-api-base", type=str)
    parser.add_argument("--gitea-url", type=str)
    parser.add_argument("--gitea-token", type=str)
    parser.add_argument("--gitea-webhook-secret", type=str)
    parser.add_argument("--config", type=str, dest="config_file")

    parsed, _ = parser.parse_known_args(args)
    return {k: v for k, v in vars(parsed).items() if v is not None}


def load_toml_file(path: str | Path | None) -> dict[str, Any]:
    """Load settings dictionary from a TOML file if present."""
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        content = file_path.read_text(encoding="utf-8")
        raw_data = tomllib.loads(content)
        # Flatten nested sections if present ([app], [gitea], [model])
        flattened: dict[str, Any] = {}
        for k, v in raw_data.items():
            if isinstance(v, dict):
                flattened.update(v)
            else:
                flattened[k] = v
        return flattened
    except Exception:
        return {}


class Settings(BaseSettings):
    """Unified runtime settings for git_bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server settings
    host: str = Field(default="0.0.0.0", description="Binding host for webhook server")
    port: int = Field(default=8080, description="Binding port for webhook server")
    max_concurrent_reviews: int = Field(
        default=5, description="Maximum simultaneous PR reviews"
    )

    # Review settings
    review_mode: ReviewMode = Field(
        default=ReviewMode.ADVISORY,
        description="Review enforcement mode ('advisory' or 'enforcing')",
    )
    comment_trigger_mode: CommentTriggerMode = Field(
        default=CommentTriggerMode.AUTONOMOUS,
        description="Comment response mode: 'autonomous' or 'mention_only'",
    )
    await_actions_completion: bool = Field(
        default=True,
        description="Wait for running CI actions before final approval",
    )
    diagnose_action_failures: bool = Field(
        default=True,
        description="Autonomously diagnose failed Action logs and post fixes",
    )
    max_action_log_chars: int = Field(
        default=15000,
        description="Max characters of failed Action log for diagnosis",
    )
    bot_name: str = Field(
        default="Gitbert", description="Name of the bot user in Gitea"
    )
    instruction: str = Field(
        default=(
            "You are Gitbert, the friendly, calm, and approachable senior developer on "
            "the team. Analyze pull request changes for logic bugs, security "
            "vulnerabilities, edge cases, and code quality with constructive feedback."
        ),
        description="System instruction for the reviewer agent",
    )

    # Model provider selection: "gemini" or "litellm"
    model_provider: ModelProvider = Field(
        default=ModelProvider.GEMINI,
        description=(
            "LLM provider: 'gemini' (native Google GenAI) or 'litellm' "
            "(agnostic OpenAI-compatible via LiteLLM)"
        ),
    )

    # Native Gemini settings
    model_name: str = Field(
        default="gemini-3.8-flash", description="Underlying Gemini LLM model name"
    )
    google_api_key: SecretStr | None = Field(
        default=None, description="Google Gemini API Key"
    )

    # OpenAI-compatible (OpenRouter, vLLM, Ollama, LiteLLM) settings
    openai_compatible_api_key: SecretStr | None = Field(
        default=None,
        description="API key for OpenAI-compatible endpoint (e.g. OpenRouter)",
    )
    openai_compatible_model: str = Field(
        default="deepseek/deepseek-v4.1-flash",
        description="Model identifier for LiteLLM / OpenAI-compatible provider",
    )
    openai_compatible_api_base: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for OpenAI-compatible endpoint",
    )

    # Gitea platform settings
    gitea_url: str = Field(
        default="http://localhost:3000", description="Base URL of the Gitea instance"
    )
    gitea_token: SecretStr | None = Field(
        default=None, description="Gitea API personal access token"
    )
    gitea_webhook_secret: SecretStr | None = Field(
        default=None, description="HMAC-SHA256 secret for webhook validation"
    )
    config_file: str | None = Field(
        default=None, description="Optional path to configuration TOML file"
    )

    def __init__(
        self,
        _toml_file: str | None = None,
        _env_file: str | None = ".env",
        **values: Any,
    ):
        # 1. Determine TOML config path
        toml_path = (
            _toml_file
            or values.get("config_file")
            or os.getenv("CONFIG_FILE")
            or ("config.toml" if Path("config.toml").is_file() else None)
        )
        file_values = load_toml_file(toml_path)

        # 2. Merge: file values as baseline, overridden by explicit keyword arguments
        combined_values = {**file_values, **values}
        super().__init__(_env_file=_env_file, **combined_values)

    @property
    def litellm_model_name(self) -> str:
        """Resolve model name formatted appropriately for LiteLLM routing."""
        model = self.openai_compatible_model
        if (
            self.openai_compatible_api_base
            and "openrouter.ai" in self.openai_compatible_api_base
            and not model.startswith("openrouter/")
        ):
            return f"openrouter/{model}"
        return model

    def get_adk_model(self) -> Any:
        """Return ADK model (str for Gemini, or LiteLlm wrapper for LiteLLM)."""
        if self.model_provider == ModelProvider.LITELLM:
            from google.adk.models.lite_llm import LiteLlm

            api_key = (
                self.openai_compatible_api_key.get_secret_value()
                if self.openai_compatible_api_key
                else None
            )
            return LiteLlm(
                model=self.litellm_model_name,
                api_key=api_key,
                api_base=self.openai_compatible_api_base,
            )
        return self.model_name

    @property
    def model(self) -> Any:
        """Alias for backward compatibility with ADK agent definitions."""
        return self.get_adk_model()

    @property
    def agent_name(self) -> str:
        """Alias for backward compatibility with ADK agent definitions."""
        return self.bot_name

    @property
    def agent_description(self) -> str:
        """Alias for backward compatibility."""
        return (
            "Gitbert: The friendly, experienced AI Senior Developer for "
            "Gitea Merge Requests."
        )


def get_settings(
    cli_args: list[str] | None = None,
    config_file: str | None = None,
    _env_file: str | None = ".env",
    **overrides: Any,
) -> Settings:
    """Factory to produce Settings instance with multi-source priority cascade."""
    cli_overrides = parse_cli_args(cli_args)
    combined = {**overrides, **cli_overrides}
    return Settings(
        _toml_file=config_file or combined.get("config_file"),
        _env_file=_env_file,
        **combined,
    )


# Default singleton instance for standard imports
settings = get_settings()
