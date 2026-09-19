"""Webhook event models."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Normalized webhook event type."""

    PR_OPENED = "pr_opened"
    PR_UPDATED = "pr_updated"
    COMMENT = "comment"
    STATUS = "status"
    IGNORED = "ignored"


class PRReviewEvent(BaseModel):
    """Normalized pull request review trigger event."""

    event_type: EventType
    platform: str
    repo: str
    pr_number: int = 0
    sender: str
    head_sha: str = ""
    base_sha: str = ""
    is_draft: bool = False
    comment_id: int | None = None
    comment_body: str | None = None
    status_state: str | None = None
    status_context: str | None = None
    target_url: str | None = None
    status_description: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
