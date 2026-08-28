from pathlib import Path

import yaml
from pydantic import BaseModel, Field
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


class MonitoringConfig(BaseModel):
    interval_seconds: int = 60
    thresholds: ThresholdSettings = Field(default_factory=ThresholdSettings)
    ssl_domains: list[str] = Field(default_factory=list)
    http_endpoints: list[HttpEndpoint] = Field(default_factory=list)


class AutoPruneConfig(BaseModel):
    enabled: bool = False  # Opt-in: destructive automation must be explicitly enabled
    trigger_percent: int = 85
    prune_builder: bool = True
    prune_dangling_images: bool = True


class AutomationConfig(BaseModel):
    auto_prune_disk: AutoPruneConfig = Field(default_factory=AutoPruneConfig)
    backup_schedule: str = "0 3 * * *"
    target_databases: list[str] = Field(default_factory=lambda: ["primary_db", "analytics_db"])


class AIConfig(BaseModel):
    enabled: bool = False
    provider: str = "openai"  # openai, anthropic, gemini, ollama
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None
    enable_rca: bool = True
    enable_log_summary: bool = True
    enable_natural_language_ops: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "production"
    log_level: str = "INFO"
    server_name: str = "node-01"
    server_timezone: str = "UTC"

    # Auth mode: "production" (default, fail-closed) or "development" (allow all — unsafe).
    # Set OPSPILOT_AUTH_MODE=development ONLY for local testing.
    auth_mode: str = "production"

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""  # Comma separated integers
    telegram_alert_chat_id: str = ""

    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    ai: AIConfig = Field(default_factory=AIConfig)

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
    return settings
