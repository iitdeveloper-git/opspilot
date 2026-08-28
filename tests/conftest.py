import pytest
from opspilot.config import Settings, MonitoringConfig


@pytest.fixture
def mock_settings():
    return Settings(
        server_name="test-server",
        telegram_allowed_user_ids="123456,789012",
        monitoring=MonitoringConfig(
            interval_seconds=10,
            ssl_domains=["example.com"],
        )
    )
