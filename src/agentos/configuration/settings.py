from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    enabled: bool = False
    api_key: SecretStr | None = None
    base_url: str | None = None

    @model_validator(mode="after")
    def _enabled_provider_has_key(self) -> "ProviderSettings":
        if self.enabled and self.api_key is None:
            raise ValueError("enabled provider requires an api_key secret reference")
        return self


class AgentOSSettings(BaseSettings):
    """Bootstrap-only settings. Secret values are deliberately redacted by Pydantic."""

    model_config = SettingsConfigDict(env_prefix="AGENTOS_", env_nested_delimiter="__", extra="ignore")

    environment: str = "development"
    app_master_key: SecretStr | None = None
    local_secret_storage_enabled: bool = False
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _master_key_required_when_storing_secrets(self) -> "AgentOSSettings":
        if self.local_secret_storage_enabled and self.app_master_key is None:
            raise ValueError("APP_MASTER_KEY is required for local secret storage")
        return self
