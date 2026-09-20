"""CLI entrypoint for Gitbert.

Commands:
- server: Start the FastAPI webhook receiver server.
- review: Run an immediate on-demand review for a specific PR.
- info: Inspect current agent configuration and status.
"""

import argparse
import asyncio
from typing import Any

from gitbert.config import create_cli_parser, get_settings
from gitbert.models.events import EventType, PRReviewEvent
from gitbert.orchestrator.engine import ReviewEngine
from gitbert.platforms.factory import get_platform_adapter
from gitbert.server import create_app


def run_server(args: argparse.Namespace) -> None:
    """Launch the webhook receiver with Uvicorn."""
    import uvicorn

    cli_overrides = {
        k: v for k, v in vars(args).items() if v is not None and k not in ("command",)
    }
    settings = get_settings(**cli_overrides)
    host = settings.host
    port = settings.port

    print("=" * 60)
    print(f"Starting Gitbert Webhook Server on {host}:{port}")
    print(f"Operational Mode:   {settings.review_mode.value.upper()}")
    print(f"Max Concurrency:    {settings.max_concurrent_reviews}")
    print(f"Target Gitea URL:   {settings.gitea_url}")
    if settings.redis_url:
        print(f"Cache Backend:      Redis/Valkey ({settings.redis_url})")
    else:
        print("Cache Backend:      In-Memory (TTL)")
    print("=" * 60)

    app = create_app(app_settings=settings)
    uvicorn.run(app, host=host, port=port, reload=False)


async def run_review_async(
    repo: str,
    pr_number: int,
    platform_name: str,
    **overrides: Any,
) -> None:
    """Run an on-demand PR review."""
    settings = get_settings(**overrides)
    platform = get_platform_adapter(platform_name, settings)
    engine = ReviewEngine(platform=platform, app_settings=settings)

    print(f"Fetching metadata for {repo} PR #{pr_number} from {platform_name}...")
    meta = await platform.get_pr_metadata(repo, pr_number)
    print(f"Title: {meta.title} (Author: {meta.author}, Head: {meta.head_sha[:8]})")
    print(f"Touched files: {[f.filename for f in meta.changed_files]}")

    event = PRReviewEvent(
        event_type=EventType.PR_OPENED,
        platform=platform_name,
        repo=repo,
        pr_number=pr_number,
        sender="cli-user",
        head_sha=meta.head_sha,
        base_sha=meta.base_sha,
    )

    print("\nExecuting review...")
    result = await engine.process_event(event)
    if result:
        print("\n" + "=" * 60)
        print(f"VERDICT: {result.decision.value}")
        print("=" * 60)
        print(result.summary)
        if result.inline_comments:
            print(f"\nInline comments ({len(result.inline_comments)}):")
            for c in result.inline_comments:
                print(f"  - {c.path}:{c.new_position} -> {c.body}")


def run_review(args: argparse.Namespace) -> None:
    """Entry point for manual PR review."""
    cli_overrides = {
        k: v
        for k, v in vars(args).items()
        if v is not None and k not in ("command", "repo", "pr", "platform")
    }
    asyncio.run(run_review_async(args.repo, args.pr, args.platform, **cli_overrides))


def print_info(args: argparse.Namespace | None = None) -> None:
    """Print current configuration status."""
    cli_overrides = (
        {k: v for k, v in vars(args).items() if v is not None and k not in ("command",)}
        if args
        else {}
    )
    settings = get_settings(**cli_overrides)
    token_status = "[Configured]" if settings.gitea_token else "[Unset]"
    wh_status = "[Configured]" if settings.gitea_webhook_secret else "[Unset]"
    key_status = "[Configured]" if settings.google_api_key else "[Unset]"

    active_model = (
        settings.openai_compatible_model
        if settings.model_provider == "litellm"
        else settings.model_name
    )
    openai_key_status = (
        "[Configured]" if settings.openai_compatible_api_key else "[Unset]"
    )

    print("=" * 60)
    print("Gitbert - Multi-Platform PR Review Agent (ADK 2.0)")
    print("=" * 60)
    print(f"Bot Name:          {settings.bot_name}")
    print(f"Review Mode:       {settings.review_mode.value.upper()}")
    print(f"Model Provider:    {settings.model_provider.value.upper()}")
    print(f"Active Model:      {active_model}")
    if settings.model_provider == "litellm":
        print(f"API Base:          {settings.openai_compatible_api_base}")
        print(f"API Key:           {openai_key_status}")
    else:
        print(f"Google API Key:    {key_status}")
    print(f"Gitea URL:         {settings.gitea_url}")
    print(f"Gitea Token:       {token_status}")
    print(f"Webhook Secret:    {wh_status}")
    if settings.redis_url:
        print(f"Cache Backend:     Redis/Valkey ({settings.redis_url})")
    else:
        print("Cache Backend:     In-Memory (TTL)")
    print("=" * 60)
    print("\nAvailable Commands:")
    print("  python main.py server                      # Start webhook listener")
    print("  python main.py review --repo org/repo --pr 12 # Run on-demand review")
    print("  uv run pytest                              # Run test suite\n")


def main() -> None:
    """CLI routing entrypoint."""
    common_parser = create_cli_parser(add_help=False)
    parser = argparse.ArgumentParser(description="Gitbert PR Reviewer CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Server command inherits all configuration flags
    subparsers.add_parser(
        "server",
        help="Start the webhook server",
        parents=[common_parser],
    )

    # Review command inherits all configuration flags
    review_parser = subparsers.add_parser(
        "review",
        help="Run review on a PR",
        parents=[common_parser],
    )
    review_parser.add_argument(
        "--repo", type=str, required=True, help="Repository in owner/repo format"
    )
    review_parser.add_argument(
        "--pr", type=int, required=True, help="Pull/Merge request number"
    )
    review_parser.add_argument(
        "--platform", type=str, default="gitea", help="Platform (default: gitea)"
    )

    # Info command
    subparsers.add_parser(
        "info",
        help="Inspect configuration status",
        parents=[common_parser],
    )

    args = parser.parse_args()

    if args.command == "server":
        run_server(args)
    elif args.command == "review":
        run_review(args)
    else:
        print_info(args)


if __name__ == "__main__":
    main()
