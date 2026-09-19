"""Per-invocation scoped review tools for ADK Agent.

Hard security boundaries:
- Repository and PR number are permanently bound from context (not LLM arguments).
- File access is locked to this repository on the MR's head commit.
- Path traversal and sensitive secret files (.env, keys) are strictly blocked.
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
        """Fetch the content of any file in the repository on this MR's branch.

        Use this to inspect imports, type definitions, utilities, or configuration
        files to get the bigger picture of how the PR changes fit into the codebase.

        Args:
            path: Relative path of the file in the repository (e.g. 'src/models.py').

        Returns:
            Full raw text content of the file at head commit.

        Raises:
            SecurityScopeViolationError: If path attempts directory traversal
                                         or matches sensitive file policy.
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
