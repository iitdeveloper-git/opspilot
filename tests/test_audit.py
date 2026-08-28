import json
import tempfile
from pathlib import Path

from opspilot.core.audit import AuditLogger


def test_audit_logger_records_action():
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = AuditLogger(log_dir=tmpdir)
        logger.record_action(
            user_id=123456, action="restart", target="api-service", status="SUCCESS", details={"reason": "memory_spike"}
        )

        log_file = Path(tmpdir) / "audit_trail.jsonl"
        assert log_file.exists()

        with open(log_file, encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["user_id"] == "123456"
            assert entry["action"] == "restart"
            assert entry["target"] == "api-service"
            assert entry["status"] == "SUCCESS"
            assert entry["details"]["reason"] == "memory_spike"
            assert "timestamp" in entry
