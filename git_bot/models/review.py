"""Review domain models for agent output."""

from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewDecision(StrEnum):
    """Review verdict decision."""

    APPROVE = "APPROVED"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


class InlineComment(BaseModel):
    """Line-specific review comment."""

    path: str = Field(description="Relative path of the target file")
    new_position: int = Field(
        description="Line number in the new/modified file to comment on"
    )
    body: str = Field(
        description="Markdown comment explaining the feedback or suggested fix"
    )


class ReviewResult(BaseModel):
    """Structured review verdict produced by the AI reviewer."""

    decision: ReviewDecision = Field(
        description="Final verdict: APPROVED, REQUEST_CHANGES, or COMMENT"
    )
    summary: str = Field(description="Markdown summary explaining the review")
    strengths: list[str] = Field(
        default_factory=list,
        description="List of positive observations and good practices found",
    )
    risks_or_concerns: list[str] = Field(
        default_factory=list,
        description="List of identified bugs, security risks, or code smells",
    )
    inline_comments: list[InlineComment] = Field(
        default_factory=list,
        description="List of targeted line-by-line comments",
    )
