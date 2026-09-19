"""CLI entrypoint for git_bot.

Commands:
- server: Start the FastAPI webhook receiver server.
- review: Run an immediate on-demand review for a specific PR.
- info: Inspect current agent configuration and status.
"""

import argparse
import asyncio

from git_bot.config import get_settings
from git_bot.models.events import EventType, PRReviewEvent
from git_bot.orchestrator.engine import ReviewEngine
from git_bot.platforms.factory import get_platform_adapter


def run_server(args: argparse.Namespace) -> None:
    """Launch the webhook receiver with Uvicorn."""
    import uvicorn

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port

    print("=" * 60)
    print(f"Starting git_bot Webhook Server on {host}:{port}")
    print(f"Operational Mode:   {settings.review_mode.value.upper()}")
    print(f"Max Concurrency:    {settings.max_concurrent_reviews}")
    print(f"Target Gitea URL:   {settings.gitea_url}")
    print("=" * 60)
    uvicorn.run("git_bot.server:app", host=host, port=port, reload=False)


async def run_review_async(repo: str, pr_number: int, platform_name: str) -> None:
    """Run an on-demand PR review."""
    settings = get_settings()
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
    asyncio.run(run_review_async(args.repo, args.pr, args.platform))


def print_info() -> None:
    """Print current configuration status."""
    settings = get_settings()
    token_status = "[Configured]" if settings.gitea_token else "[Unset]"
    wh_status = "[Configured]" if settings.gitea_webhook_secret else "[Unset]"
    key_status = "[Configured]" if settings.google_api_key else "[Unset]"

    print("=" * 60)
    print("git_bot - Multi-Platform PR Review Agent (ADK 2.0)")
    print("=" * 60)
    print(f"Bot Name:          {settings.bot_name}")
    print(f"Review Mode:       {settings.review_mode.value.upper()}")
    print(f"Model:             {settings.model_name}")
    print(f"Gitea URL:         {settings.gitea_url}")
    print(f"Gitea Token:       {token_status}")
    print(f"Webhook Secret:    {wh_status}")
    print(f"Google API Key:    {key_status}")
    print("=" * 60)
    print("\nAvailable Commands:")
    print("  python main.py server                      # Start webhook listener")
    print("  python main.py review --repo org/repo --pr 12 # Run on-demand review")
    print("  uv run pytest                              # Run test suite\n")


def main() -> None:
    """CLI routing entrypoint."""
    parser = argparse.ArgumentParser(description="git_bot PR Reviewer CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Server command
    server_parser = subparsers.add_parser("server", help="Start the webhook server")
    server_parser.add_argument("--host", type=str, help="Binding host")
    server_parser.add_argument("--port", type=int, help="Binding port")

    # Review command
    review_parser = subparsers.add_parser("review", help="Run review on a PR")
    review_parser.add_argument(
        "--repo", type=str, required=True, help="Repository in owner/repo format"
    )
    review_parser.add_argument(
        "--pr", type=int, required=True, help="Pull/Merge request number"
    )
    review_parser.add_argument(
        "--platform", type=str, default="gitea", help="Platform (default: gitea)"
    )

    args = parser.parse_args()

    if args.command == "server":
        run_server(args)
    elif args.command == "review":
        run_review(args)
    else:
        print_info()


if __name__ == "__main__":
    main()
