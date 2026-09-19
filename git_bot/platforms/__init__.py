"""Code platforms abstraction package."""

from git_bot.platforms.base import ICodePlatform
from git_bot.platforms.factory import get_platform_adapter
from git_bot.platforms.gitea import GiteaAdapter

__all__ = ["GiteaAdapter", "ICodePlatform", "get_platform_adapter"]
