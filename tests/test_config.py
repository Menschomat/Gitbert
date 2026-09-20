"""Tests for tiered multi-source configuration."""

from pydantic import SecretStr

from gitbert.config import ReviewMode, Settings, get_settings


def test_default_settings():
    """Verify default settings are loaded with sensible values."""
    settings = Settings(_env_file=None)
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.max_concurrent_reviews == 5
    assert settings.review_mode == ReviewMode.ADVISORY
    assert settings.bot_name == "Gitbert"
    assert settings.model_name == "gemini-3.8-flash"
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


def test_comment_trigger_mode_default_and_override(monkeypatch):
    """Verify comment_trigger_mode defaults to AUTONOMOUS and can be overridden."""
    from gitbert.config import CommentTriggerMode

    # 1. Default is AUTONOMOUS (Option B)
    settings = Settings(_env_file=None)
    assert settings.comment_trigger_mode == CommentTriggerMode.AUTONOMOUS

    # 2. Env var override
    monkeypatch.setenv("COMMENT_TRIGGER_MODE", "mention_only")
    env_settings = get_settings(_env_file=None)
    assert env_settings.comment_trigger_mode == CommentTriggerMode.MENTION_ONLY

    # 3. CLI override
    cli_settings = get_settings(
        cli_args=["--comment-trigger-mode", "autonomous"],
        _env_file=None,
    )
    assert cli_settings.comment_trigger_mode == CommentTriggerMode.AUTONOMOUS


def test_action_settings_default_and_override(monkeypatch):
    """Verify action completion and diagnosis settings defaults and overrides."""
    settings = Settings(_env_file=None)
    assert settings.await_actions_completion is True
    assert settings.diagnose_action_failures is True
    assert settings.max_action_log_chars == 15000

    monkeypatch.setenv("AWAIT_ACTIONS_COMPLETION", "false")
    monkeypatch.setenv("DIAGNOSE_ACTION_FAILURES", "false")
    env_settings = get_settings(_env_file=None)
    assert env_settings.await_actions_completion is False
    assert env_settings.diagnose_action_failures is False

    cli_settings = get_settings(
        cli_args=["--no-await-actions-completion", "--no-diagnose-action-failures"],
        _env_file=None,
    )
    assert cli_settings.await_actions_completion is False
    assert cli_settings.diagnose_action_failures is False


def test_model_provider_settings_default_and_override(monkeypatch):
    """Verify model provider and OpenAI-compatible settings defaults and overrides."""
    from google.adk.models.lite_llm import LiteLlm

    from gitbert.config import ModelProvider

    settings = Settings(_env_file=None)
    assert settings.model_provider == ModelProvider.GEMINI
    assert settings.model_name == "gemini-3.8-flash"
    assert settings.get_adk_model() == "gemini-3.8-flash"

    # Env overrides
    monkeypatch.setenv("MODEL_PROVIDER", "litellm")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-openrouter-secret")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "openrouter/mistralai/mistral-large")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_BASE", "https://openrouter.ai/api/v1")

    env_settings = get_settings(_env_file=None)
    assert env_settings.model_provider == ModelProvider.LITELLM
    assert env_settings.openai_compatible_model == "openrouter/mistralai/mistral-large"
    assert env_settings.openai_compatible_api_base == "https://openrouter.ai/api/v1"
    assert (
        env_settings.openai_compatible_api_key.get_secret_value()
        == "sk-openrouter-secret"
    )
    assert "sk-openrouter-secret" not in repr(env_settings)

    adk_model = env_settings.get_adk_model()
    assert isinstance(adk_model, LiteLlm)
    assert adk_model.model == "openrouter/mistralai/mistral-large"
