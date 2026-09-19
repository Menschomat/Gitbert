"""Entrypoint for git_bot."""

from git_bot.agent import root_agent
from git_bot.tools import get_current_time


def main() -> None:
    """Entry point to inspect agent configuration or test tools."""
    tools_list = [getattr(t, "__name__", str(t)) for t in root_agent.tools]

    print("=" * 60)
    print(f"Agent Name:        {root_agent.name}")
    print(f"Model:             {root_agent.model}")
    print(f"Configured Tools:  {tools_list}")
    print("=" * 60)

    print("\nTesting time tool directly:")
    result = get_current_time()
    print(f"Current UTC Time:  {result['readable']} ({result['iso']})")

    print("\nNext steps:")
    print("  1. Copy .env.example to .env and set your GOOGLE_API_KEY")
    print("  2. Run interactive chat:")
    print("       uv run adk run git_bot")
    print("  3. Run ADK Web UI:")
    print("       uv run adk web .")
    print()


if __name__ == "__main__":
    main()
