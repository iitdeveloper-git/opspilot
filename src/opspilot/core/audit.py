import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("opspilot.audit")


class AuditLogger:
    def __init__(self, log_dir: str = "audit_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "audit_trail.jsonl"

    def record_action(
        self,
        user_id: int | str,
        action: str,
        target: str,
        status: str,
        details: dict | None = None
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": str(user_id),
            "action": action,
            "target": target,
            "status": status,
            "details": details or {},
        }
        logger.info(f"[AUDIT] {user_id} | {action} -> {target} [{status}]")
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "
")
        except Exception as e:
            logger.error(f"Failed to write audit trail: {e}")
