"""Tests for scoped review tools."""

from unittest.mock import AsyncMock

import pytest

from git_bot.models.platform import ChangedFile, PRMetadata
from git_bot.platforms.base import ICodePlatform
from git_bot.security.context import ScopedMRContext
from git_bot.security.exceptions import SecurityScopeViolationError
from git_bot.tools.scoped_review_tools import create_scoped_tools


@pytest.fixture
def mock_platform():
    """Mock implementation of ICodePlatform."""
    platform = AsyncMock(spec=ICodePlatform)
    platform.get_pr_diff.return_value = "+print('hello world')"
    platform.get_file_content.return_value = "def hello():\n    pass\n"
    platform.get_pr_metadata.return_value = PRMetadata(
        repo="myorg/repo",
        number=10,
        title="Add greetings",
        author="alice",
        base_ref="main",
        base_sha="base-sha",
        head_ref="feat/hello",
        head_sha="head-sha",
        changed_files=[ChangedFile(filename="src/hello.py")],
    )
    return platform


@pytest.fixture
def scoped_context():
    """Scoped context locked to PR #10 with src/hello.py allowlisted."""
    return ScopedMRContext(
        platform="gitea",
        repo="myorg/repo",
        pr_number=10,
        head_sha="head-sha",
        base_sha="base-sha",
        allowed_files=frozenset(["src/hello.py"]),
    )


@pytest.mark.asyncio
async def test_scoped_get_pr_diff(mock_platform, scoped_context):
    """Verify get_pr_diff calls platform with locked repo and PR number."""
    tools = create_scoped_tools(scoped_context, mock_platform)
    get_diff_tool = next(t for t in tools if t.__name__ == "get_pr_diff")

    diff = await get_diff_tool()
    assert diff == "+print('hello world')"
    mock_platform.get_pr_diff.assert_called_once_with("myorg/repo", 10)


@pytest.mark.asyncio
async def test_scoped_get_file_content_allowed(mock_platform, scoped_context):
    """Verify get_file_content succeeds for files in the allowlist."""
    tools = create_scoped_tools(scoped_context, mock_platform)
    get_file_tool = next(t for t in tools if t.__name__ == "get_file_content")

    content = await get_file_tool("src/hello.py")
    assert "def hello():" in content
    mock_platform.get_file_content.assert_called_with(
        "myorg/repo", "src/hello.py", "head-sha"
    )

    # Reading unmodified repository files for context should also succeed
    await get_file_tool("pyproject.toml")
    mock_platform.get_file_content.assert_called_with(
        "myorg/repo", "pyproject.toml", "head-sha"
    )


@pytest.mark.asyncio
async def test_scoped_get_file_content_unauthorized(mock_platform, scoped_context):
    """Verify get_file_content rejects unauthorized files."""
    tools = create_scoped_tools(scoped_context, mock_platform)
    get_file_tool = next(t for t in tools if t.__name__ == "get_file_content")

    with pytest.raises(SecurityScopeViolationError):
        await get_file_tool(".env")

    with pytest.raises(SecurityScopeViolationError):
        await get_file_tool("../../etc/passwd")


@pytest.mark.asyncio
async def test_scoped_get_pr_metadata(mock_platform, scoped_context):
    """Verify get_pr_metadata returns locked PR metadata."""
    tools = create_scoped_tools(scoped_context, mock_platform)
    get_meta_tool = next(t for t in tools if t.__name__ == "get_pr_metadata")

    meta = await get_meta_tool()
    assert meta["number"] == 10
    assert meta["title"] == "Add greetings"
    assert "src/hello.py" in meta["allowed_files"]


@pytest.mark.asyncio
async def test_scoped_list_repository_files(mock_platform, scoped_context):
    """Verify list_repository_files navigates directory and filters sensitive files."""
    mock_platform.list_directory.return_value = [
        {"name": "app.py", "path": "src/app.py", "type": "file", "size": 100},
        {"name": ".env", "path": ".env", "type": "file", "size": 50},
        {"name": "utils", "path": "src/utils", "type": "dir", "size": 0},
    ]

    tools = create_scoped_tools(scoped_context, mock_platform)
    list_tool = next(t for t in tools if t.__name__ == "list_repository_files")

    items = await list_tool("src")
    # .env should be filtered out from directory listing
    item_names = [i["name"] for i in items]
    assert "app.py" in item_names
    assert "utils" in item_names
    assert ".env" not in item_names

    # Traversal attempts should be blocked
    with pytest.raises(SecurityScopeViolationError):
        await list_tool("../../")
