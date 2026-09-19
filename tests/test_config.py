"""Tests for tiered multi-source configuration."""

from pydantic import SecretStr

from git_bot.config import ReviewMode, Settings, get_settings


def test_default_settings():
    """Verify default settings are loaded with sensible values."""
    settings = Settings(_env_file=None)
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.max_concurrent_reviews == 5
    assert settings.review_mode == ReviewMode.ADVISORY
    assert settings.bot_name == "git_bot"
    assert settings.model_name == "gemini-2.0-flash"
    assert settings.gitea_url == "http://localhost:3000"
    assert settings.gitea_token is None


def test_env_var_override(monkeypatch):
    """Verify environment variables override default settings."""
    monkeypatch.setenv("GITEA_URL", "https://gitea.mycompany.org")
    monkeypatch.setenv("REVIEW_MODE", "enforcing")
    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setenv("GITEA_TOKEN", "secret-token-123")
    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "10")

    settings = get_settings(_env_file=None)
    assert settings.gitea_url == "https://gitea.mycompany.org"
    assert settings.review_mode == ReviewMode.ENFORCING
    assert settings.port == 9090
    assert settings.max_concurrent_reviews == 10
    assert isinstance(settings.gitea_token, SecretStr)
    assert settings.gitea_token.get_secret_value() == "secret-token-123"


def test_toml_file_loading(tmp_path):
    """Verify settings can be loaded from a TOML configuration file."""
    config_content = """
host = "127.0.0.1"
port = 7070
review_mode = "enforcing"
max_concurrent_reviews = 3
gitea_url = "https://git.internal.net"
"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content, encoding="utf-8")

    settings = Settings(_toml_file=str(config_file), _env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 7070
    assert settings.review_mode == ReviewMode.ENFORCING
    assert settings.max_concurrent_reviews == 3
    assert settings.gitea_url == "https://git.internal.net"


def test_cli_override(monkeypatch):
    """Verify CLI argument overrides take precedence over config files and env vars."""
    monkeypatch.setenv("PORT", "8888")

    settings = get_settings(
        cli_args=["--port", "9999", "--review-mode", "enforcing"],
        _env_file=None,
    )
    assert settings.port == 9999
    assert settings.review_mode == ReviewMode.ENFORCING


def test_secret_str_masking():
    """Verify sensitive tokens are masked in repr and str to prevent logging leaks."""
    settings = Settings(
        gitea_token=SecretStr("super-secret-token"),
        google_api_key=SecretStr("ai-secret-key"),
        _env_file=None,
    )
    assert "super-secret-token" not in str(settings)
    assert "super-secret-token" not in repr(settings)
    assert "ai-secret-key" not in str(settings)
    assert "ai-secret-key" not in repr(settings)
