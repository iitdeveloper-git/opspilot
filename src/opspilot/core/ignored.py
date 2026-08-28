import json
import logging
from pathlib import Path

logger = logging.getLogger("opspilot.ignored")


class IgnoredContainersManager:
    """Manages persistent list of container names ignored from automated health alerts."""

    def __init__(self, file_path: str = "audit_logs/ignored_containers.json"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ignored: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.file_path.exists():
            try:
                with open(self.file_path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._ignored = set(data)
            except Exception as e:
                logger.error(f"Failed to load ignored containers list: {e}")

    def _save(self) -> None:
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(sorted(list(self._ignored)), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save ignored containers list: {e}")

    def is_ignored(self, container_name: str) -> bool:
        return container_name in self._ignored

    def ignore(self, container_name: str) -> bool:
        if container_name not in self._ignored:
            self._ignored.add(container_name)
            self._save()
            logger.info(f"Container '{container_name}' added to ignored alerts list.")
            return True
        return False

    def unignore(self, container_name: str) -> bool:
        if container_name in self._ignored:
            self._ignored.remove(container_name)
            self._save()
            logger.info(f"Container '{container_name}' removed from ignored alerts list.")
            return True
        return False

    def list_ignored(self) -> list[str]:
        return sorted(list(self._ignored))
