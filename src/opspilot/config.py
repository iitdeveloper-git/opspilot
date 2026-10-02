"""OpsPilot 2.0 configuration — fully backward compatible."""

from pathlib import Path
from typing import Self

import yaml
from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ThresholdSettings(BaseModel):
    disk_percent_warning: int = 80
    disk_percent_critical: int = 90
    ram_percent_critical: int = 92
    cpu_percent_critical: int = 95


class HttpEndpoint(BaseModel):
    name: str
    url: str
    expected_status: int = 200
    timeout_seconds: int = 5


class RenewalItem(BaseModel):
    """Static renewal entry configurable in config.yaml (seeded to DB on first run)."""

    name: str
    category: str = "other"  # vps | domain | ssl | software | other
    due_date: str  # ISO8601: "2026-10-15"
    amount: float | None = None
    currency: str = "INR"
    notes: str = ""
    recurrence: str = "none"  # none | monthly | yearly
    remind_days_before: int = 7


class MonitoringConfig(BaseModel):
    interval_seconds: int = 60
    thresholds: ThresholdSettings = Field(default_factory=ThresholdSettings)
    ssl_domains: list[str] = Field(default_factory=list)
    http_endpoints: list[HttpEndpoint] = Field(default_factory=list)
    initial_renewals: list[RenewalItem] = Field(default_factory=list)


class AutoPruneConfig(BaseModel):
    enabled: bool = False
    trigger_percent: int = 85
    prune_builder: bool = True
    prune_dangling_images: bool = True


class AutomationConfig(BaseModel):
    auto_prune_disk: AutoPruneConfig = Field(default_factory=AutoPruneConfig)
    backup_schedule: str = "0 3 * * *"
    target_databases: list[str] = Field(default_factory=lambda: ["primary_db", "analytics_db"])


class AIConfig(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None
    enable_rca: bool = True
    enable_log_summary: bool = True
    enable_natural_language_ops: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(default="production", validation_alias=AliasChoices("environment", "opspilot_environment"))
    log_level: str = "INFO"
    server_name: str = Field(default="node-01", validation_alias=AliasChoices("server_name", "opspilot_server_name"))
    server_timezone: str = "UTC"
    auth_mode: str = Field(default="production", validation_alias=AliasChoices("auth_mode", "opspilot_auth_mode"))
    db_path: str = Field(default="data/opspilot.db", validation_alias=AliasChoices("db_path", "opspilot_db_path"))

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_alert_chat_id: str = ""

    # Web Command Center (Disabled by default, 127.0.0.1 only)
    web_enabled: bool = Field(default=False, validation_alias=AliasChoices("web_enabled", "opspilot_web_enabled"))
    web_host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("web_host", "opspilot_web_host"))
    web_port: int = Field(default=8088, validation_alias=AliasChoices("web_port", "opspilot_web_port"))
    admin_password: str = Field(default="", validation_alias=AliasChoices("admin_password", "opspilot_admin_password"))

    @model_validator(mode="after")
    def validate_web_security(self) -> Self:
        if self.web_enabled:
            pwd = self.admin_password.strip()
            if not pwd:
                raise ValueError("OPSPILOT_ADMIN_PASSWORD must be set when web_enabled=True.")
            if len(pwd) < 12:
                raise ValueError("OPSPILOT_ADMIN_PASSWORD must be at least 12 characters long for security.")
        return self

    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    ai: AIConfig = Field(default_factory=AIConfig)

    # Flat AI settings for direct .env / environment binding
    ai_enabled: bool = Field(default=False, validation_alias=AliasChoices("ai_enabled", "opspilot_ai_enabled"))
    ai_provider: str = Field(default="", validation_alias=AliasChoices("ai_provider", "opspilot_ai_provider"))
    ai_model: str = Field(default="", validation_alias=AliasChoices("ai_model", "opspilot_ai_model"))
    ai_api_key: str = Field(
        default="", validation_alias=AliasChoices("ai_api_key", "gemini_api_key", "opspilot_ai_api_key")
    )
    ai_base_url: str = Field(default="", validation_alias=AliasChoices("ai_base_url", "opspilot_ai_base_url"))

    @model_validator(mode="after")
    def sync_ai_settings(self) -> Self:
        if self.ai_api_key and not self.ai.api_key:
            self.ai.api_key = self.ai_api_key
        if self.ai_provider:
            self.ai.provider = self.ai_provider
        if self.ai_model:
            self.ai.model = self.ai_model
        if self.ai_base_url:
            self.ai.base_url = self.ai_base_url
        if self.ai_enabled:
            self.ai.enabled = self.ai_enabled
        elif self.ai.api_key and self.ai.api_key != "sk-...":
            self.ai.enabled = True
        return self

    @property
    def allowed_users(self) -> set[int]:
        if not self.telegram_allowed_user_ids:
            return set()
        return {int(x.strip()) for x in self.telegram_allowed_user_ids.split(",") if x.strip().isdigit()}


def load_settings(config_path: str | Path | None = None) -> Settings:
    settings = Settings()
    path = Path(config_path or "config.yaml")
    if path.exists():
        with open(path) as f:
            yaml_data = yaml.safe_load(f) or {}
            server_cfg = yaml_data.get("server", {})
            if "name" in server_cfg:
                settings.server_name = server_cfg["name"]
            if "environment" in server_cfg:
                settings.environment = server_cfg["environment"]
            if "timezone" in server_cfg:
                settings.server_timezone = server_cfg["timezone"]
            if "monitoring" in yaml_data:
                settings.monitoring = MonitoringConfig(**yaml_data["monitoring"])
            if "automation" in yaml_data:
                settings.automation = AutomationConfig(**yaml_data["automation"])
            if "ai" in yaml_data:
                settings.ai = AIConfig(**yaml_data["ai"])
            if "db_path" in yaml_data:
                settings.db_path = yaml_data["db_path"]
            web_cfg = yaml_data.get("web", {})
            if "enabled" in web_cfg:
                settings.web_enabled = bool(web_cfg["enabled"])
            if "host" in web_cfg:
                settings.web_host = str(web_cfg["host"])
            if "port" in web_cfg:
                settings.web_port = int(web_cfg["port"])
    return settings
