import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("opspilot.ignored")


def parse_duration(duration_str: str | None) -> timedelta | None:
    """Parse duration strings like '1h', '24h', '7d', '30m', 'forever' into a timedelta (or None for indefinite)."""
    if not duration_str:
        return None
    d = duration_str.strip().lower()
    if d in ["forever", "indefinite", "infinite", "none", "always", "until"]:
        return None

    match = re.match(r"^(\d+)\s*(m|min|minute|minutes|h|hr|hour|hours|d|day|days|w|week|weeks)$", d)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if unit.startswith("m"):
        return timedelta(minutes=amount)
    elif unit.startswith("h"):
        return timedelta(hours=amount)
    elif unit.startswith("d"):
        return timedelta(days=amount)
    elif unit.startswith("w"):
        return timedelta(weeks=amount)
    return None


class IgnoredContainersManager:
    """Manages persistent list of container names ignored from automated health alerts,

    with support for time-based snooze / expiry.
    """

    def __init__(self, file_path: str = "audit_logs/ignored_containers.json"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        # Internal store: dict[container_name -> {"ignored_at": ISO, "expires_at": ISO | None, "duration_str": str}]
        self._entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.file_path.exists():
            try:
                with open(self.file_path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # Backward compatibility: old format was list[str]
                        now_iso = datetime.now(UTC).isoformat()
                        self._entries = {
                            name: {"ignored_at": now_iso, "expires_at": None, "duration_str": "forever"}
                            for name in data
                        }
                    elif isinstance(data, dict):
                        self._entries = data
            except Exception as e:
                logger.error(f"Failed to load ignored containers list: {e}")

    def _save(self) -> None:
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save ignored containers list: {e}")

    def _cleanup_expired(self) -> bool:
        """Remove any expired snooze entries. Returns True if any entries were purged."""
        now = datetime.now(UTC)
        expired = []
        for name, info in self._entries.items():
            expires_at_str = info.get("expires_at")
            if expires_at_str:
                try:
                    exp_dt = datetime.fromisoformat(expires_at_str)
                    if now >= exp_dt:
                        expired.append(name)
                except Exception:
                    pass

        if expired:
            for name in expired:
                del self._entries[name]
                logger.info(f"Snooze expired for container '{name}'. Automated alerts resumed.")
            self._save()
            return True
        return False

    def is_ignored(self, container_name: str) -> bool:
        self._cleanup_expired()
        return container_name in self._entries

    def ignore(self, container_name: str, duration_str: str | None = None) -> tuple[bool, str]:
        """Ignore a container with an optional snooze duration (e.g. '1h', '24h', '7d', 'forever').

        Returns (success: bool, human_readable_description: str).
        """
        self._cleanup_expired()
        now = datetime.now(UTC)
        td = parse_duration(duration_str)

        if td is not None:
            expires_at = now + td
            expires_at_str = expires_at.isoformat()
            duration_desc = f"{duration_str} (until {expires_at.strftime('%Y-%m-%d %H:%M UTC')})"
            dur_key = duration_str or "custom"
        else:
            expires_at_str = None
            duration_desc = "indefinitely (until /unignore)"
            dur_key = "forever"

        self._entries[container_name] = {
            "ignored_at": now.isoformat(),
            "expires_at": expires_at_str,
            "duration_str": dur_key,
        }
        self._save()
        logger.info(f"Container '{container_name}' ignored for {duration_desc}.")
        return True, duration_desc

    def unignore(self, container_name: str) -> bool:
        self._cleanup_expired()
        if container_name in self._entries:
            del self._entries[container_name]
            self._save()
            logger.info(f"Container '{container_name}' unignored.")
            return True
        return False

    def list_ignored_details(self) -> list[dict]:
        """Return list of active ignored containers with remaining time information."""
        self._cleanup_expired()
        now = datetime.now(UTC)
        results = []

        for name, info in sorted(self._entries.items()):
            expires_at_str = info.get("expires_at")
            if expires_at_str:
                try:
                    exp_dt = datetime.fromisoformat(expires_at_str)
                    diff = exp_dt - now
                    if diff.total_seconds() > 0:
                        hrs, rem = divmod(int(diff.total_seconds()), 3600)
                        mins, _ = divmod(rem, 60)
                        remaining = f"{hrs}h {mins}m left" if hrs > 0 else f"{mins}m left"
                    else:
                        remaining = "expiring now"
                except Exception:
                    remaining = "active"
            else:
                remaining = "Indefinite"

            results.append(
                {
                    "name": name,
                    "duration_str": info.get("duration_str", "forever"),
                    "remaining": remaining,
                    "expires_at": expires_at_str,
                }
            )
        return results

    def list_ignored(self) -> list[str]:
        self._cleanup_expired()
        return sorted(list(self._entries.keys()))
