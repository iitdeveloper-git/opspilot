import tempfile
from pathlib import Path

from opspilot.core.ignored import IgnoredContainersManager


def test_ignored_containers_manager_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = str(Path(tmpdir) / "ignored.json")
        manager = IgnoredContainersManager(file_path=file_path)

        assert manager.list_ignored() == []
        assert manager.is_ignored("stalwart-mailserver") is False

        # Ignore container
        assert manager.ignore("stalwart-mailserver") is True
        assert manager.is_ignored("stalwart-mailserver") is True
        assert manager.list_ignored() == ["stalwart-mailserver"]

        # Duplicate ignore returns False
        assert manager.ignore("stalwart-mailserver") is False

        # Reload manager from file to verify persistence
        manager_reloaded = IgnoredContainersManager(file_path=file_path)
        assert manager_reloaded.is_ignored("stalwart-mailserver") is True

        # Unignore
        assert manager_reloaded.unignore("stalwart-mailserver") is True
        assert manager_reloaded.is_ignored("stalwart-mailserver") is False
        assert manager_reloaded.list_ignored() == []

        # Duplicate unignore returns False
        assert manager_reloaded.unignore("stalwart-mailserver") is False
