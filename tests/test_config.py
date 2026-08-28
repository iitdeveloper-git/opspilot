from pathlib import Path

from opspilot.config import Settings, load_settings


def test_default_settings():
    settings = Settings()
    assert settings.auth_mode == "production"
    assert settings.automation.auto_prune_disk.enabled is False
    assert settings.environment == "production"
    assert settings.server_name == "node-01"


def test_allowed_users_property():
    settings = Settings(telegram_allowed_user_ids="123, 456 , 789")
    assert settings.allowed_users == {123, 456, 789}

    empty_settings = Settings(telegram_allowed_user_ids="")
    assert empty_settings.allowed_users == set()


def test_load_settings_from_example_yaml():
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    assert example_path.exists(), "config.example.yaml must exist as configuration contract"

    settings = load_settings(example_path)
    assert settings.server_name == "node-01"
    assert settings.environment == "production"
    assert settings.server_timezone == "UTC"
    assert settings.automation.auto_prune_disk.enabled is False
    assert settings.automation.auto_prune_disk.trigger_percent == 85
    assert isinstance(settings.monitoring.ssl_domains, list)
    assert isinstance(settings.monitoring.http_endpoints, list)
