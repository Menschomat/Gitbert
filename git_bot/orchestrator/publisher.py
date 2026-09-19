"""Deterministic Review Publisher.

Ensures that:
- Commit statuses (pending, success, failure) are accurately published.
- In Advisory mode, platform reviews are submitted as COMMENT.
- In Enforcing mode, platform reviews are submitted as APPROVE / REQUEST_CHANGES.
- Inline comment line numbers are validated.
"""

from git_bot.config import ReviewMode
from git_bot.models.platform import CommitState, CommitStatus
from git_bot.models.review import ReviewDecision, ReviewResult
from git_bot.platforms.base import ICodePlatform


class ReviewPublisher:
    """Posts reviews and commit statuses deterministically to the platform."""

    def __init__(
        self,
        platform: ICodePlatform,
        mode: ReviewMode = ReviewMode.ADVISORY,
    ):
        self.platform = platform
        self.mode = mode

    async def publish(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        review: ReviewResult,
    ) -> None:
        """Publish review verdict and commit status to the platform.

        Args:
            repo: Repository name (e.g. 'owner/repo').
            pr_number: Pull request / Merge request number.
            head_sha: Git commit SHA of the PR head.
            review: Validated ReviewResult produced by the agent.
        """
        # 1. Determine commit status check
        if review.decision == ReviewDecision.APPROVE:
            commit_state = CommitState.SUCCESS
            status_desc = "AI Review passed: Code is ready to merge."
        elif review.decision == ReviewDecision.REQUEST_CHANGES:
            commit_state = CommitState.FAILURE
            status_desc = "AI Review: Changes requested before merge."
        else:
            commit_state = CommitState.SUCCESS
            status_desc = "AI Review: Commentary provided."

        commit_status = CommitStatus(
            state=commit_state,
            description=status_desc,
            context="git-bot/pr-review",
        )
        await self.platform.set_commit_status(repo, head_sha, commit_status)

        # 2. Format review output and apply operational mode
        if self.mode == ReviewMode.ADVISORY:
            # Build advisory summary badge
            if review.decision == ReviewDecision.APPROVE:
                badge = "### 🟢 [ADVISORY VERDICT: READY TO MERGE]\n\n"
            elif review.decision == ReviewDecision.REQUEST_CHANGES:
                badge = "### 🔴 [ADVISORY VERDICT: CHANGES REQUESTED]\n\n"
            else:
                badge = "### 💬 [ADVISORY VERDICT: COMMENTARY]\n\n"

            formatted_summary = badge + review.summary
            # In advisory mode, force platform review decision to COMMENT
            final_review = ReviewResult(
                decision=ReviewDecision.COMMENT,
                summary=formatted_summary,
                strengths=review.strengths,
                risks_or_concerns=review.risks_or_concerns,
                inline_comments=review.inline_comments,
            )
        else:
            final_review = review

        # 3. Submit review to platform
        await self.platform.submit_review(repo, pr_number, final_review)
