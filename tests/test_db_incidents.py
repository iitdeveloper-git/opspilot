"""Tests for db/incidents.py — Fix #2 (datetime format), Fix #3 (snooze doesn't re-alert)."""

from datetime import UTC, datetime, timedelta

import pytest

from opspilot.db.engine import init_db, set_db_path
from opspilot.db.incidents import (
    count_open_incidents,
    list_open_incidents,
    open_incident,
    prune_old_incidents,
    resolve_incident,
    resolve_incident_by_id,
    snooze_incident,
)


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    await init_db()


# ─── Fix #3: snooze doesn't create duplicate + re-alert ──────────────────────


async def test_snooze_incident_suppresses_alert():
    """Snoozed incident → open_incident returns is_snoozed=True, not is_new=True."""
    inc_id, is_new, is_snoozed = await open_incident("http_probe", "https://test.com", "critical", "down")
    assert is_new and not is_snoozed

    until = datetime.now(UTC) + timedelta(hours=2)
    await snooze_incident(inc_id, until)

    # Same target goes down again during snooze window
    inc_id2, is_new2, is_snoozed2 = await open_incident("http_probe", "https://test.com", "critical", "still down")
    assert inc_id2 == inc_id, "Must reuse the same incident row"
    assert not is_new2
    assert is_snoozed2, "Must signal caller that alert is suppressed"


async def test_snoozed_incident_not_new_row():
    """Ensures no duplicate incident row is created while snoozed."""
    inc_id, _, _ = await open_incident("docker", "my_app", "critical", "exited")
    until = datetime.now(UTC) + timedelta(hours=1)
    await snooze_incident(inc_id, until)

    for _ in range(3):
        await open_incident("docker", "my_app", "critical", "still exited")

    count = await count_open_incidents()
    assert count == 1, "Only one open incident expected"


async def test_snooze_expires_rearms_alert():
    """After snooze window passes, open_incident should return is_snoozed=False."""
    inc_id, _, _ = await open_incident("http_probe", "https://x.com", "critical", "down")
    # Snooze that has already expired
    expired = datetime.now(UTC) - timedelta(hours=1)
    await snooze_incident(inc_id, expired)

    _, is_new2, is_snoozed2 = await open_incident("http_probe", "https://x.com", "critical", "still down")
    assert not is_new2
    assert not is_snoozed2, "Expired snooze should re-arm the alert"


# ─── Dedupe ───────────────────────────────────────────────────────────────────


async def test_open_incident_deduplication():
    id1, new1, _ = await open_incident("docker", "api", "critical", "down")
    id2, new2, _ = await open_incident("docker", "api", "critical", "still down")
    assert id1 == id2
    assert new1 is True
    assert new2 is False


# ─── Auto-resolve ─────────────────────────────────────────────────────────────


async def test_resolve_incident_clears_open():
    await open_incident("docker", "worker", "critical", "exited")
    resolved = await resolve_incident("docker", "worker")
    assert resolved is not None
    open_list = await list_open_incidents()
    assert len(open_list) == 0


async def test_resolve_returns_none_if_not_found():
    result = await resolve_incident("docker", "nonexistent")
    assert result is None


# ─── Fix #2: datetime format stored without timezone suffix ──────────────────


async def test_snooze_format_is_sql_comparable():
    """
    Verify the stored snoozed_until value is comparable with datetime('now') in SQLite.
    The value must NOT contain a '+00:00' suffix which would break text comparison.
    """
    from opspilot.db.engine import db_conn

    inc_id, _, _ = await open_incident("http_probe", "https://format-test.com", "critical", "down")
    until = datetime.now(UTC) + timedelta(hours=2)
    await snooze_incident(inc_id, until)

    async with db_conn() as db:
        cursor = await db.execute("SELECT snoozed_until FROM incidents WHERE id=?", (inc_id,))
        row = await cursor.fetchone()
        stored = row["snoozed_until"]

    assert "+" not in stored, f"Stored value has timezone suffix: {stored!r}"
    assert "T" not in stored, f"Stored value uses ISO T separator: {stored!r}"
    # Must be in 'YYYY-MM-DD HH:MM:SS' format
    datetime.strptime(stored, "%Y-%m-%d %H:%M:%S")  # raises if format wrong


# ─── Pruning ──────────────────────────────────────────────────────────────────


async def test_prune_old_incidents_removes_resolved():
    inc_id, _, _ = await open_incident("http_probe", "https://old.com", "warning", "slow")
    await resolve_incident_by_id(inc_id)
    # Force resolved_at to 31 days ago
    from opspilot.db.engine import db_conn

    async with db_conn() as db:
        await db.execute("UPDATE incidents SET resolved_at=datetime('now','-31 days') WHERE id=?", (inc_id,))
        await db.commit()
    pruned = await prune_old_incidents(days=30)
    assert pruned == 1
