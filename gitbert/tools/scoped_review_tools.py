"""Per-invocation scoped review tools for ADK Agent.

Hard security boundaries:
- Repository and PR number are permanently bound from context (not LLM arguments).
- File access is locked to this repository on the MR's head commit.
- Path traversal and sensitive secret files (.env, keys) are strictly blocked.
"""

from collections.abc import Callable
from typing import Any

from gitbert.platforms.base import ICodePlatform
from gitbert.security.context import ScopedMRContext
from gitbert.security.exceptions import SecurityScopeViolationError


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

    async def list_repository_files(
        directory: str = "",
    ) -> list[dict[str, Any]]:
        """List files and subdirectories in a directory on this MR's branch.

        Use this navigation tool to discover project structure, locate imported modules,
        or find related test files.

        Args:
            directory: Directory path relative to repo root (default: "" for root).

        Returns:
            List of entries with 'name', 'path', 'type' ('file' or 'dir'), and 'size'.
        """
        validated_dir = context.validate_directory_access(directory)
        raw_items = await platform.list_directory(
            context.repo, validated_dir, context.head_sha
        )
        safe_items: list[dict[str, Any]] = []
        for item in raw_items:
            path = item.get("path", "")
            try:
                if item.get("type") == "file":
                    context.validate_file_access(path)
                safe_items.append(item)
            except SecurityScopeViolationError:
                continue
        return safe_items

    async def get_pr_comments() -> list[dict[str, Any]]:
        """Retrieve all discussion comments and review comments on this MR.

        Use this tool to read past conversations, identify previous bot reviews
        (is_bot: true), check whether past feedback was addressed, and avoid
        repeating identical comments.

        Returns:
            List of comments with 'id', 'author', 'is_bot', 'comment_type',
            'body', 'created_at', 'path', and 'line'.
        """
        comments = await platform.get_pr_comments(context.repo, context.pr_number)
        return [
            {
                "id": c.id,
                "author": c.author,
                "is_bot": c.is_bot,
                "comment_type": c.comment_type.value,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
                "path": c.path,
                "line": c.line,
            }
            for c in comments
        ]

    return [
        get_pr_diff,
        get_file_content,
        get_pr_metadata,
        list_repository_files,
        get_pr_comments,
    ]
