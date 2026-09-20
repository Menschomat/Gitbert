"""Platform adapter factory."""

from gitbert.config import Settings, get_settings
from gitbert.platforms.base import ICodePlatform
from gitbert.platforms.gitea import GiteaAdapter


def get_platform_adapter(
    platform_name: str = "gitea",
    app_settings: Settings | None = None,
) -> ICodePlatform:
    """Factory to instantiate code platform adapter based on name and settings."""
    cfg = app_settings or get_settings()

    if platform_name.lower() == "gitea":
        return GiteaAdapter(
            base_url=cfg.gitea_url,
            token=cfg.gitea_token,
            webhook_secret=cfg.gitea_webhook_secret,
            bot_name=cfg.bot_name,
        )

    raise ValueError(f"Unsupported code platform: '{platform_name}'")
