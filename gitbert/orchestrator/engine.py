"""Review Engine Orchestrator coordinating PR review lifecycle."""

import json
import logging
from collections.abc import Callable
from typing import Any

from gitbert.agent.prompts import (
    ACTION_DIAGNOSTIC_INSTRUCTION,
    COMMENT_RESPONDER_INSTRUCTION,
    REVIEWER_SYSTEM_INSTRUCTION,
)
from gitbert.config import (
    CommentTriggerMode,
    ModelProvider,
    Settings,
    get_settings,
)
from gitbert.models.events import EventType, PRReviewEvent
from gitbert.models.platform import CommitState, CommitStatus
from gitbert.models.review import (
    ActionDiagnosticResult,
    CommentResponse,
    ReviewDecision,
    ReviewResult,
)
from gitbert.orchestrator.publisher import ReviewPublisher
from gitbert.platforms.base import ICodePlatform
from gitbert.security.context import ScopedMRContext

logger = logging.getLogger(__name__)


class ReviewEngine:
    """Orchestrates incoming PR events, security scoping, review, and publishing."""

    def __init__(
        self,
        platform: ICodePlatform,
        app_settings: Settings | None = None,
        analyzer: Callable[..., Any] | None = None,
        responder: Callable[..., Any] | None = None,
        diagnostician: Callable[..., Any] | None = None,
    ):
        self.platform = platform
        self.settings = app_settings or get_settings()
        self.publisher = ReviewPublisher(
            platform=self.platform,
            mode=self.settings.review_mode,
        )
        self._custom_analyzer = analyzer
        self._custom_responder = responder
        self._custom_diagnostician = diagnostician
        self._cached_reviews: dict[str, ReviewResult] = {}

    async def analyze_pr(self, context: ScopedMRContext, diff: str) -> ReviewResult:
        """Analyze PR diff and generate structured ReviewResult."""
        if self._custom_analyzer is not None:
            return await self._custom_analyzer(context, diff)

        # 1. Native Google Gemini mode
        if (
            self.settings.model_provider == ModelProvider.GEMINI
            and self.settings.google_api_key
        ):
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

        # 2. Agnostic LiteLLM / OpenAI-compatible mode (OpenRouter, vLLM, etc.)
        elif self.settings.model_provider == ModelProvider.LITELLM and (
            self.settings.openai_compatible_api_key
            or self.settings.openai_compatible_api_base
        ):
            try:
                import litellm

                api_key = (
                    self.settings.openai_compatible_api_key.get_secret_value()
                    if self.settings.openai_compatible_api_key
                    else None
                )
                prompt = (
                    f"Review MR #{context.pr_number} in repo '{context.repo}'.\n\n"
                    f"Allowed files: {list(context.allowed_files)}\n\n"
                    f"Diff:\n```\n{diff[:30000]}\n```\n\n"
                    f"{REVIEWER_SYSTEM_INSTRUCTION}"
                )
                response = await litellm.acompletion(
                    model=self.settings.openai_compatible_model,
                    messages=[{"role": "user", "content": prompt}],
                    api_key=api_key,
                    api_base=self.settings.openai_compatible_api_base,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content
                if content:
                    parsed = json.loads(content)
                    return ReviewResult.model_validate(parsed)
            except Exception as exc:
                logger.warning(
                    "Failed to generate review via LiteLLM, falling back: %s", exc
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
            bot_handle in comment_body.lower()
            or "@gitbert" in comment_body.lower()
            or "@git_bot" in comment_body.lower()
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

        # 1. Native Gemini evaluation
        if (
            self.settings.model_provider == ModelProvider.GEMINI
            and self.settings.google_api_key
        ):
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

        # 2. Agnostic LiteLLM / OpenAI-compatible evaluation
        elif self.settings.model_provider == ModelProvider.LITELLM and (
            self.settings.openai_compatible_api_key
            or self.settings.openai_compatible_api_base
        ):
            try:
                import litellm

                api_key = (
                    self.settings.openai_compatible_api_key.get_secret_value()
                    if self.settings.openai_compatible_api_key
                    else None
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
                res = await litellm.acompletion(
                    model=self.settings.openai_compatible_model,
                    messages=[{"role": "user", "content": prompt}],
                    api_key=api_key,
                    api_base=self.settings.openai_compatible_api_base,
                    response_format={"type": "json_object"},
                )
                content = res.choices[0].message.content
                if content:
                    parsed = json.loads(content)
                    decision = CommentResponse.model_validate(parsed)
                    if decision.should_reply and decision.reply:
                        await self.platform.post_pr_comment(
                            event.repo, event.pr_number, decision.reply
                        )
                    return decision
            except Exception as exc:
                logger.warning("Failed to evaluate comment via LiteLLM: %s", exc)

        # Fallback / heuristic response when mentioned without external LLM
        if is_mentioned:
            fallback_reply = (
                f"Hello @{event.sender}! Gitbert here. I received your mention "
                f"regarding PR #{event.pr_number}. I will incorporate your feedback "
                "into the next review cycle."
            )
            resp = CommentResponse(
                should_reply=True,
                reply=fallback_reply,
                reasoning="Direct mention fallback response.",
            )
            await self.platform.post_pr_comment(event.repo, event.pr_number, resp.reply)
            return resp

        return None

    async def diagnose_action_failure(
        self, repo: str, pr_number: int, context: str, log_content: str
    ) -> ActionDiagnosticResult | None:
        """Diagnose a failed Action run using the diagnostic prompt or hook."""
        if self._custom_diagnostician is not None:
            return await self._custom_diagnostician(
                repo, pr_number, context, log_content
            )

        # 1. Native Gemini diagnosis
        if (
            self.settings.model_provider == ModelProvider.GEMINI
            and self.settings.google_api_key
        ):
            try:
                from google import genai

                client = genai.Client(
                    api_key=self.settings.google_api_key.get_secret_value()
                )
                prompt = (
                    f"Action '{context}' failed on PR #{pr_number} "
                    f"in repo '{repo}'.\n\n"
                    f"Failure Log:\n```\n{log_content}\n```\n\n"
                    f"{ACTION_DIAGNOSTIC_INSTRUCTION}"
                )
                res = client.models.generate_content(
                    model=self.settings.model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": ActionDiagnosticResult,
                    },
                )
                if res.text:
                    return ActionDiagnosticResult.model_validate_json(res.text)
            except Exception as exc:
                logger.warning("Failed to diagnose action via Gemini: %s", exc)

        # 2. Agnostic LiteLLM / OpenAI-compatible diagnosis
        elif self.settings.model_provider == ModelProvider.LITELLM and (
            self.settings.openai_compatible_api_key
            or self.settings.openai_compatible_api_base
        ):
            try:
                import litellm

                api_key = (
                    self.settings.openai_compatible_api_key.get_secret_value()
                    if self.settings.openai_compatible_api_key
                    else None
                )
                prompt = (
                    f"Action '{context}' failed on PR #{pr_number} "
                    f"in repo '{repo}'.\n\n"
                    f"Failure Log:\n```\n{log_content}\n```\n\n"
                    f"{ACTION_DIAGNOSTIC_INSTRUCTION}"
                )
                res = await litellm.acompletion(
                    model=self.settings.openai_compatible_model,
                    messages=[{"role": "user", "content": prompt}],
                    api_key=api_key,
                    api_base=self.settings.openai_compatible_api_base,
                    response_format={"type": "json_object"},
                )
                content = res.choices[0].message.content
                if content:
                    return ActionDiagnosticResult.model_validate_json(content)
            except Exception as exc:
                logger.warning("Failed to diagnose action via LiteLLM: %s", exc)

        return ActionDiagnosticResult(
            context=context,
            diagnosis="Action exited with a non-zero exit code during CI execution.",
            suggested_fix="Inspect the job failure logs and ensure all tests pass.",
            related_files=[],
        )

    async def handle_status_event(self, event: PRReviewEvent) -> None:
        """Handle commit status updates from CI / Gitea Actions."""
        pr_number = event.pr_number
        if not pr_number and event.head_sha:
            pr_number = (
                await self.platform.find_pr_for_commit(event.repo, event.head_sha) or 0
            )

        if not pr_number:
            logger.info(
                "No matching open PR for commit %s in %s",
                event.head_sha,
                event.repo,
            )
            return

        statuses = await self.platform.get_commit_statuses(event.repo, event.head_sha)
        external = [s for s in statuses if not s.context.startswith("git-bot/")]

        # If any check is still pending, wait
        if any(s.state == CommitState.PENDING for s in external):
            logger.info(
                "PR #%d still has running CI checks, awaiting completion.",
                pr_number,
            )
            return

        failed_checks = [
            s for s in external if s.state in (CommitState.FAILURE, CommitState.ERROR)
        ]

        cache_key = f"{event.repo}:{pr_number}"
        cached_review = self._cached_reviews.get(cache_key)

        if failed_checks:
            failed = failed_checks[0]
            if self.settings.diagnose_action_failures:
                log_content = ""
                if failed.target_url:
                    raw_log = await self.platform.get_action_log(
                        event.repo, failed.target_url
                    )
                    max_chars = self.settings.max_action_log_chars
                    log_content = raw_log[-max_chars:] if raw_log else ""

                diag = await self.diagnose_action_failure(
                    event.repo, pr_number, failed.context, log_content
                )
                if diag:
                    comment_body = (
                        f"### ❌ CI Action Failed: `{diag.context}`\n\n"
                        f"**Diagnosis**:\n{diag.diagnosis}\n\n"
                        f"**Suggested Fix**:\n{diag.suggested_fix}"
                    )
                    await self.platform.post_pr_comment(
                        event.repo, pr_number, comment_body
                    )

            fail_status = CommitStatus(
                state=CommitState.FAILURE,
                description=f"CI Action failed: {failed.context}",
                context="git-bot/pr-review",
            )
            await self.platform.set_commit_status(
                event.repo, event.head_sha, fail_status
            )
        else:
            # All CI checks succeeded!
            if cached_review and cached_review.decision == ReviewDecision.APPROVE:
                final_status = CommitStatus(
                    state=CommitState.SUCCESS,
                    description="All code reviews and CI checks passed!",
                    context="git-bot/pr-review",
                )
                await self.platform.set_commit_status(
                    event.repo, event.head_sha, final_status
                )
                if self.settings.review_mode.value == "enforcing":
                    await self.platform.submit_review(
                        event.repo, pr_number, cached_review
                    )

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

        if event.event_type == EventType.STATUS:
            logger.info(
                "Processing status event (%s: %s) on %s",
                event.status_context,
                event.status_state,
                event.repo,
            )
            await self.handle_status_event(event)
            return None

        if event.is_draft:
            logger.info(
                "Skipping review for draft PR #%d (%s)", event.pr_number, event.repo
            )
            return None

        # 1. Publish initial PENDING commit status
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
        cache_key = f"{event.repo}:{event.pr_number}"
        self._cached_reviews[cache_key] = review

        # 5. Check if external CI actions are currently running
        statuses = await self.platform.get_commit_statuses(event.repo, event.head_sha)
        external = [s for s in statuses if not s.context.startswith("git-bot/")]
        has_pending = any(s.state == CommitState.PENDING for s in external)

        if has_pending and self.settings.await_actions_completion:
            # Two-phase mode: publish advisory comments, keep status PENDING
            notice_summary = (
                f"{review.summary}\n\n"
                "> ⏳ **Notice**: External CI Actions are currently running. "
                "Final approval verdict will be submitted once all checks complete."
            )
            advisory = ReviewResult(
                decision=ReviewDecision.COMMENT,
                summary=notice_summary,
                strengths=review.strengths,
                risks_or_concerns=review.risks_or_concerns,
                inline_comments=review.inline_comments,
            )
            await self.platform.submit_review(event.repo, event.pr_number, advisory)
            hold_status = CommitStatus(
                state=CommitState.PENDING,
                description="Code review complete; awaiting CI Actions...",
                context="git-bot/pr-review",
            )
            await self.platform.set_commit_status(
                event.repo, event.head_sha, hold_status
            )
            return review

        # Classic direct mode: publish final verdict immediately
        await self.publisher.publish(
            repo=event.repo,
            pr_number=event.pr_number,
            head_sha=event.head_sha,
            review=review,
        )

        return review
