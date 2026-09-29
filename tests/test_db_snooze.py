"""Tests for db/snooze.py — Fix #2 (datetime format) and snooze expiry."""

from datetime import UTC, datetime, timedelta

import pytest

from opspilot.db.engine import init_db, set_db_path
from opspilot.db.snooze import (
    cleanup_expired_snoozes,
    is_snoozed,
    list_snoozed,
    snooze_container,
    unsnooze_container,
)


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    await init_db()


# ─── Fix #2: correct format stored ───────────────────────────────────────────


async def test_snooze_format_has_no_timezone_suffix():
    """Stored expires_at must NOT have +00:00 suffix — it must compare with datetime('now')."""
    from opspilot.db.engine import db_conn

    exp = datetime.now(UTC) + timedelta(hours=2)
    await snooze_container("test_app", exp)

    async with db_conn() as db:
        cursor = await db.execute("SELECT expires_at FROM container_snooze WHERE container_name='test_app'")
        row = await cursor.fetchone()
        stored = row["expires_at"]

    assert "+" not in stored, f"Timezone suffix found: {stored!r}"
    assert "T" not in stored, f"ISO T separator found: {stored!r}"
    datetime.strptime(stored, "%Y-%m-%d %H:%M:%S")


# ─── Active snooze ────────────────────────────────────────────────────────────


async def test_is_snoozed_active():
    exp = datetime.now(UTC) + timedelta(hours=1)
    await snooze_container("redis", exp)
    assert await is_snoozed("redis")


async def test_is_snoozed_forever():
    await snooze_container("celery", None)
    assert await is_snoozed("celery")


# ─── Expiry ───────────────────────────────────────────────────────────────────


async def test_expired_snooze_not_active():
    """
    Core regression for Fix #2: a snooze that expired 30 min ago must NOT be active.
    Pre-fix this would return True because '+00:00' sorts after ' ' in text comparison.
    """
    already_expired = datetime.now(UTC) - timedelta(minutes=30)
    await snooze_container("nginx", already_expired)
    assert not await is_snoozed("nginx"), "Expired snooze must NOT be treated as active"


async def test_cleanup_removes_expired():
    expired = datetime.now(UTC) - timedelta(minutes=5)
    await snooze_container("expired_svc", expired)
    await snooze_container("active_svc", datetime.now(UTC) + timedelta(hours=1))
    removed = await cleanup_expired_snoozes()
    assert removed == 1
    assert not await is_snoozed("expired_svc")
    assert await is_snoozed("active_svc")


# ─── Unsnooze ─────────────────────────────────────────────────────────────────


async def test_unsnooze():
    await snooze_container("worker", None)
    assert await is_snoozed("worker")
    await unsnooze_container("worker")
    assert not await is_snoozed("worker")


# ─── List ─────────────────────────────────────────────────────────────────────


async def test_list_snoozed_only_active():
    await snooze_container("active", datetime.now(UTC) + timedelta(hours=3))
    await snooze_container("expired", datetime.now(UTC) - timedelta(hours=1))
    entries = await list_snoozed()
    names = [e["name"] for e in entries]
    assert "active" in names
    assert "expired" not in names
