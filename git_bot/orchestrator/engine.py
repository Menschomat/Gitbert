"""Review Engine Orchestrator coordinating PR review lifecycle."""

import json
import logging
from collections.abc import Callable
from typing import Any

from git_bot.agent.prompts import COMMENT_RESPONDER_INSTRUCTION
from git_bot.config import CommentTriggerMode, Settings, get_settings
from git_bot.models.events import EventType, PRReviewEvent
from git_bot.models.platform import CommitState, CommitStatus
from git_bot.models.review import CommentResponse, ReviewDecision, ReviewResult
from git_bot.orchestrator.publisher import ReviewPublisher
from git_bot.platforms.base import ICodePlatform
from git_bot.security.context import ScopedMRContext

logger = logging.getLogger(__name__)


class ReviewEngine:
    """Orchestrates incoming PR events, security scoping, review, and publishing."""

    def __init__(
        self,
        platform: ICodePlatform,
        app_settings: Settings | None = None,
        analyzer: Callable[..., Any] | None = None,
        responder: Callable[..., Any] | None = None,
    ):
        self.platform = platform
        self.settings = app_settings or get_settings()
        self.publisher = ReviewPublisher(
            platform=self.platform,
            mode=self.settings.review_mode,
        )
        self._custom_analyzer = analyzer
        self._custom_responder = responder

    async def analyze_pr(self, context: ScopedMRContext, diff: str) -> ReviewResult:
        """Analyze PR diff and generate structured ReviewResult."""
        if self._custom_analyzer is not None:
            return await self._custom_analyzer(context, diff)

        # If Google API key is configured, use Gemini with structured output
        if self.settings.google_api_key:
            try:
                from google import genai

                client = genai.Client(
                    api_key=self.settings.google_api_key.get_secret_value()
                )
                prompt = (
                    f"Review MR #{context.pr_number} in repo '{context.repo}'.\n\n"
                    f"Allowed files: {list(context.allowed_files)}\n\n"
                    f"Diff:\n```\n{diff[:30000]}\n```\n"
                )
                response = client.models.generate_content(
                    model=self.settings.model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": ReviewResult,
                    },
                )
                if response.text:
                    parsed = json.loads(response.text)
                    return ReviewResult.model_validate(parsed)
            except Exception as exc:
                logger.warning(
                    "Failed to generate review via Gemini, falling back: %s", exc
                )

        # Baseline heuristic review when API key is unset or as fallback
        has_tests = any("test" in f.lower() for f in context.allowed_files)
        diff_lines = diff.splitlines()
        additions = sum(
            1
            for line in diff_lines
            if line.startswith("+") and not line.startswith("+++")
        )

        file_count = len(context.allowed_files)
        summary = (
            f"Automated baseline review for MR #{context.pr_number}. "
            f"Total files: {file_count}, additions: ~{additions} lines."
        )
        strengths = [f"Modified {file_count} scoped files."]
        if has_tests:
            strengths.append("Contains corresponding test modifications.")

        risks: list[str] = []
        if not has_tests and additions > 20:
            risks.append("No test files detected for non-trivial code additions.")

        decision = (
            ReviewDecision.APPROVE if not risks else ReviewDecision.REQUEST_CHANGES
        )

        return ReviewResult(
            decision=decision,
            summary=summary,
            strengths=strengths,
            risks_or_concerns=risks,
            inline_comments=[],
        )

    async def evaluate_and_reply_comment(
        self, event: PRReviewEvent
    ) -> CommentResponse | None:
        """Evaluate a user comment on a PR and post a reply if appropriate."""
        comment_body = (event.comment_body or "").strip()
        if not comment_body:
            return None

        # Check mention
        bot_handle = f"@{self.settings.bot_name.lower()}"
        is_mentioned = (
            bot_handle in comment_body.lower() or "@git_bot" in comment_body.lower()
        )

        # If MENTION_ONLY mode (Option A) and not mentioned, do nothing
        if (
            self.settings.comment_trigger_mode == CommentTriggerMode.MENTION_ONLY
            and not is_mentioned
        ):
            logger.info(
                "Ignoring comment #%s on PR #%d: not mentioned in MENTION_ONLY mode",
                event.comment_id,
                event.pr_number,
            )
            return None

        # Custom responder hook (for testing and custom implementations)
        if self._custom_responder is not None:
            response: CommentResponse | None = await self._custom_responder(event)
            if response and response.should_reply and response.reply:
                await self.platform.post_pr_comment(
                    event.repo, event.pr_number, response.reply
                )
            return response

        # If Google Gemini API key is configured
        if self.settings.google_api_key:
            try:
                from google import genai

                client = genai.Client(
                    api_key=self.settings.google_api_key.get_secret_value()
                )
                prior_comments = await self.platform.get_pr_comments(
                    event.repo, event.pr_number
                )
                comments_snippet = "\n".join(
                    f"- [{c.author}] ({'BOT' if c.is_bot else 'USER'}): {c.body[:200]}"
                    for c in prior_comments[-5:]
                )

                prompt = (
                    f"A developer commented on PR #{event.pr_number} "
                    f"in repo '{event.repo}'.\n\n"
                    f'New Comment by @{event.sender}:\n"{comment_body}"\n\n'
                    f"Recent Conversation History:\n{comments_snippet}\n\n"
                    f"{COMMENT_RESPONDER_INSTRUCTION}"
                )
                res = client.models.generate_content(
                    model=self.settings.model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": CommentResponse,
                    },
                )
                if res.text:
                    parsed = json.loads(res.text)
                    decision = CommentResponse.model_validate(parsed)
                    if decision.should_reply and decision.reply:
                        await self.platform.post_pr_comment(
                            event.repo, event.pr_number, decision.reply
                        )
                    return decision
            except Exception as exc:
                logger.warning("Failed to evaluate comment via Gemini: %s", exc)

        # Fallback / heuristic response when mentioned without external LLM
        if is_mentioned:
            fallback_reply = (
                f"Hello @{event.sender}! I received your mention regarding "
                f"PR #{event.pr_number}. I will incorporate your feedback into "
                "the next review cycle."
            )
            resp = CommentResponse(
                should_reply=True,
                reply=fallback_reply,
                reasoning="Direct mention fallback response.",
            )
            await self.platform.post_pr_comment(event.repo, event.pr_number, resp.reply)
            return resp

        return None

    async def process_event(self, event: PRReviewEvent) -> ReviewResult | None:
        """Process incoming PR review or comment event through the full lifecycle."""
        if event.event_type == EventType.IGNORED:
            logger.info("Ignoring event for PR #%d (%s)", event.pr_number, event.repo)
            return None

        if event.event_type == EventType.COMMENT:
            logger.info(
                "Processing comment event #%s on PR #%d (%s)",
                event.comment_id,
                event.pr_number,
                event.repo,
            )
            await self.evaluate_and_reply_comment(event)
            return None

        if event.is_draft:
            logger.info(
                "Skipping review for draft PR #%d (%s)", event.pr_number, event.repo
            )
            return None

        # 1. Publish PENDING commit status
        pending_status = CommitStatus(
            state=CommitState.PENDING,
            description="AI Code Review in progress...",
            context="git-bot/pr-review",
        )
        await self.platform.set_commit_status(
            event.repo, event.head_sha, pending_status
        )

        # 2. Fetch metadata and build ScopedMRContext
        meta = await self.platform.get_pr_metadata(event.repo, event.pr_number)
        allowed_files = frozenset(f.filename for f in meta.changed_files)

        context = ScopedMRContext(
            platform=event.platform,
            repo=event.repo,
            pr_number=event.pr_number,
            head_sha=event.head_sha,
            base_sha=event.base_sha,
            allowed_files=allowed_files,
        )

        # 3. Fetch PR diff
        diff = await self.platform.get_pr_diff(event.repo, event.pr_number)

        # 4. Analyze PR
        review = await self.analyze_pr(context, diff)

        # 5. Publish review verdict and final commit status
        await self.publisher.publish(
            repo=event.repo,
            pr_number=event.pr_number,
            head_sha=event.head_sha,
            review=review,
        )

        return review
