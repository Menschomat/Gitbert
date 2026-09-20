"""Platform domain models."""

from enum import StrEnum

from pydantic import BaseModel, Field


class CommitState(StrEnum):
    """Commit status state."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"


class CommitStatus(BaseModel):
    """Commit status check payload."""

    state: CommitState
    description: str
    context: str = Field(default="gitbert/pr-review")
    target_url: str | None = None


class ChangedFile(BaseModel):
    """Information about a file modified in a pull request."""

    filename: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0


class PRMetadata(BaseModel):
    """Normalized metadata for a pull request / merge request."""

    repo: str
    number: int
    title: str
    body: str = ""
    author: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    is_draft: bool = False
    changed_files: list[ChangedFile] = Field(default_factory=list)
