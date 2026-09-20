"""Code platforms abstraction package."""

from gitbert.platforms.base import ICodePlatform
from gitbert.platforms.factory import get_platform_adapter
from gitbert.platforms.gitea import GiteaAdapter

__all__ = ["GiteaAdapter", "ICodePlatform", "get_platform_adapter"]
