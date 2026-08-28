import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from opspilot.core.ignored import IgnoredContainersManager, parse_duration


def test_parse_duration():
    assert parse_duration("1h") == timedelta(hours=1)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("forever") is None
    assert parse_duration("indefinite") is None
    assert parse_duration(None) is None


def test_ignored_containers_manager_with_duration():
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = str(Path(tmpdir) / "ignored.json")
        manager = IgnoredContainersManager(file_path=file_path)

        assert manager.list_ignored() == []
        assert manager.is_ignored("stalwart-mailserver") is False

        # Ignore for 1h
        ok, desc = manager.ignore("stalwart-mailserver", "1h")
        assert ok is True
        assert "1h" in desc
        assert manager.is_ignored("stalwart-mailserver") is True

        details = manager.list_ignored_details()
        assert len(details) == 1
        assert details[0]["name"] == "stalwart-mailserver"
        assert "left" in details[0]["remaining"] or "m left" in details[0]["remaining"]

        # Reload manager from file to verify persistence
        manager_reloaded = IgnoredContainersManager(file_path=file_path)
        assert manager_reloaded.is_ignored("stalwart-mailserver") is True

        # Unignore
        assert manager_reloaded.unignore("stalwart-mailserver") is True
        assert manager_reloaded.is_ignored("stalwart-mailserver") is False


def test_ignored_containers_manager_auto_expiry():
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = str(Path(tmpdir) / "ignored.json")
        manager = IgnoredContainersManager(file_path=file_path)

        # Inject an entry that already expired in the past
        past_iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        manager._entries["expired-container"] = {
            "ignored_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "expires_at": past_iso,
            "duration_str": "1m",
        }
        manager._save()

        # is_ignored should automatically purge it and return False
        assert manager.is_ignored("expired-container") is False
        assert manager.list_ignored() == []
