"""Webhook event models."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Normalized webhook event type."""

    PR_OPENED = "pr_opened"
    PR_UPDATED = "pr_updated"
    COMMENT = "comment"
    IGNORED = "ignored"


class PRReviewEvent(BaseModel):
    """Normalized pull request review trigger event."""

    event_type: EventType
    platform: str
    repo: str
    pr_number: int
    sender: str
    head_sha: str
    base_sha: str
    is_draft: bool = False
    raw_payload: dict[str, Any] = Field(default_factory=dict)
