"""Tests for the time tool."""

import re

from git_bot.tools.time_tool import get_current_time


def test_get_current_time_default_utc():
    """Test get_current_time with default UTC timezone."""
    result = get_current_time()

    assert isinstance(result, dict)
    assert result["timezone"] == "UTC"
    assert "iso" in result
    assert "date" in result
    assert "time" in result
    assert "readable" in result

    # Check date format YYYY-MM-DD
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["date"])
    # Check time format HH:MM:SS
    assert re.match(r"^\d{2}:\d{2}:\d{2}$", result["time"])


def test_get_current_time_custom_timezone():
    """Test get_current_time with a valid custom timezone."""
    result = get_current_time("Europe/Berlin")

    assert isinstance(result, dict)
    assert result["timezone"] == "Europe/Berlin"
    assert "iso" in result


def test_get_current_time_invalid_timezone_fallback():
    """Test get_current_time gracefully falls back to UTC on invalid timezone."""
    result = get_current_time("Invalid/NonExistent_Zone")

    assert isinstance(result, dict)
    assert "fallback" in result["timezone"].lower()
    assert "iso" in result
