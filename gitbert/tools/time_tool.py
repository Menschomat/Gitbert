"""Time tool for git_bot."""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_current_time(timezone_name: str | None = None) -> dict[str, Any]:
    """Get the current date and time, optionally for a specific timezone.

    Args:
        timezone_name: Optional IANA timezone name
                       (e.g. 'UTC', 'Europe/Berlin', 'America/New_York').
                       If omitted or invalid, UTC is used as fallback.

    Returns:
        Dictionary containing current timestamp, timezone name, ISO8601 string,
        and human-readable representation.
    """
    if timezone_name:
        try:
            tz = ZoneInfo(timezone_name)
            tz_str = timezone_name
        except (ZoneInfoNotFoundError, ValueError):
            tz = UTC
            tz_str = f"UTC (fallback, '{timezone_name}' not recognized)"
    else:
        tz = UTC
        tz_str = "UTC"

    now = datetime.now(tz)
    return {
        "timezone": tz_str,
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "readable": now.strftime("%A, %B %d, %Y %I:%M:%S %p %Z"),
    }
