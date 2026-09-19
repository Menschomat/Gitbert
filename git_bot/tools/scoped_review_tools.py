"""Per-invocation scoped review tools for ADK Agent.

Hard security boundaries:
- Repository and PR number are permanently bound from context (not LLM arguments).
- File access is strictly validated against context.allowed_files.
"""

from collections.abc import Callable
from typing import Any

from git_bot.platforms.base import ICodePlatform
from git_bot.security.context import ScopedMRContext


def create_scoped_tools(
    context: ScopedMRContext,
    platform: ICodePlatform,
) -> list[Callable[..., Any]]:
    """Generate isolated, read-only tools bound strictly to the specified MR.

    Args:
        context: ScopedMRContext locking the session to a specific MR.
        platform: Platform adapter (Gitea, GitHub, GitLab).

    Returns:
        List of callable tool functions ready for Google ADK Agent.
    """

    async def get_pr_diff() -> str:
        """Fetch the unified diff of all code changes in the current Merge Request."""
        return await platform.get_pr_diff(context.repo, context.pr_number)

    async def get_file_content(path: str) -> str:
        """Fetch the full content of a file modified in this Merge Request.

        Args:
            path: Relative path of the file (must be part of this MR's changes).

        Returns:
            Full raw text content of the file at head commit.

        Raises:
            SecurityScopeViolationError: If path is not part of this MR's allowlist.
        """
        validated_path = context.validate_file_access(path)
        return await platform.get_file_content(
            context.repo, validated_path, context.head_sha
        )

    async def get_pr_metadata() -> dict[str, Any]:
        """Fetch title, author, description, and list of modified files for this MR."""
        meta = await platform.get_pr_metadata(context.repo, context.pr_number)
        return {
            "number": meta.number,
            "title": meta.title,
            "description": meta.body,
            "author": meta.author,
            "base_ref": meta.base_ref,
            "head_ref": meta.head_ref,
            "allowed_files": sorted(context.allowed_files),
        }

    return [get_pr_diff, get_file_content, get_pr_metadata]
