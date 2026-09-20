"""Comment domain models for PR discussions and review threads."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class CommentType(StrEnum):
    """Classification of a comment."""

    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"


class PRComment(BaseModel):
    """Normalized comment on a pull request."""

    id: int
    author: str
    is_bot: bool = False
    comment_type: CommentType = CommentType.ISSUE_COMMENT
    body: str
    created_at: datetime
    path: str | None = None
    line: int | None = None
    reply_to_id: int | None = None
