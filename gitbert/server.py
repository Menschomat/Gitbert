"""FastAPI Webhook Server with HMAC validation and concurrent review gate."""

import asyncio
import json
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from gitbert.config import Settings, get_settings
from gitbert.models.events import EventType, PRReviewEvent
from gitbert.orchestrator.engine import ReviewEngine
from gitbert.platforms.base import ICodePlatform
from gitbert.platforms.factory import get_platform_adapter

logger = logging.getLogger(__name__)


def create_app(
    app_settings: Settings | None = None,
    platform: ICodePlatform | None = None,
    engine: ReviewEngine | None = None,
) -> FastAPI:
    """Application factory for FastAPI webhook receiver."""
    cfg = app_settings or get_settings()
    plat = platform or get_platform_adapter("gitea", cfg)
    review_engine = engine or ReviewEngine(platform=plat, app_settings=cfg)

    # Concurrency gate
    semaphore = asyncio.Semaphore(cfg.max_concurrent_reviews)

    app = FastAPI(
        title="Gitbert PR Reviewer",
        description="Automated AI Code Reviewer for Gitea Merge Requests",
        version="0.1.0",
    )

    @app.get("/healthz")
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "ok",
            "bot_name": cfg.bot_name,
            "mode": cfg.review_mode.value,
            "max_concurrency": cfg.max_concurrent_reviews,
        }

    async def _execute_review_task(event: PRReviewEvent) -> None:
        """Run review workflow bounded by concurrency semaphore."""
        async with semaphore:
            try:
                logger.info(
                    "Starting review for PR #%d on %s", event.pr_number, event.repo
                )
                await review_engine.process_event(event)
                logger.info(
                    "Completed review for PR #%d on %s", event.pr_number, event.repo
                )
            except Exception as exc:
                logger.exception(
                    "Error executing review for PR #%d: %s", event.pr_number, exc
                )
                if (
                    event.event_type in (EventType.PR_OPENED, EventType.PR_UPDATED)
                    and event.head_sha
                ):
                    try:
                        from gitbert.models.platform import CommitState, CommitStatus

                        status_context = f"{cfg.bot_name.lower()}/pr-review"
                        fail_status = CommitStatus(
                            state=CommitState.FAILURE,
                            description="AI Review encountered an internal error.",
                            context=status_context,
                        )
                        await plat.set_commit_status(
                            event.repo, event.head_sha, fail_status
                        )
                    except Exception as status_exc:
                        logger.error(
                            "Failed to update commit status on error for PR #%d: %s",
                            event.pr_number,
                            status_exc,
                        )

    @app.post("/webhook/gitea")
    async def gitea_webhook(
        request: Request, background_tasks: BackgroundTasks
    ) -> Response:
        """Gitea webhook endpoint."""
        raw_body = await request.body()
        headers = dict(request.headers)

        # 1. Verify webhook signature
        if not plat.verify_webhook(headers, raw_body):
            raise HTTPException(
                status_code=401, detail="Invalid webhook signature or token"
            )

        # 2. Parse JSON payload
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Malformed JSON payload")

        # 3. Parse domain event
        event = plat.parse_event(headers, payload)
        if event is None or event.event_type == EventType.IGNORED:
            return JSONResponse(content={"status": "ignored"}, status_code=200)

        # 4. Dispatch background review task
        background_tasks.add_task(_execute_review_task, event)

        # 5. Immediately return 202 Accepted
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "repo": event.repo,
                "pr_number": event.pr_number,
                "event": event.event_type.value,
            },
        )

    return app


# Default app instance for uvicorn
app = create_app()
