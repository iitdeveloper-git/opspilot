"""
CRUD for the incidents table.

Fix #2: all datetimes stored as '%Y-%m-%d %H:%M:%S' UTC (no +00:00 suffix)
Fix #3: open_incident finds the existing incident regardless of snooze state,
        then returns is_snoozed so callers can decide whether to alert.
"""

from __future__ import annotations

from datetime import UTC, datetime

from opspilot.db.engine import db_conn


def _to_sql_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


async def open_incident(
    source: str,
    target: str,
    severity: str,
    title: str,
    detail: str = "",
) -> tuple[int, bool, bool]:
    """
    Insert a new incident or bump alert_count on the existing open one.
    Returns (incident_id, is_new, is_snoozed).

    is_new=True  → first time this target broke; caller should alert.
    is_snoozed   → existing incident is still within its snooze window; caller should NOT alert.
    """
    async with db_conn() as db:
        # Find open incident for source+target regardless of snooze status
        cursor = await db.execute(
            """SELECT id, snoozed_until FROM incidents
               WHERE source = ? AND target = ? AND resolved_at IS NULL
               LIMIT 1""",
            (source, target),
        )
        existing = await cursor.fetchone()

        if existing:
            inc_id = existing["id"]
            snoozed_until_str = existing["snoozed_until"]
            # Check if still snoozed
            is_snoozed = False
            if snoozed_until_str:
                try:
                    snooze_dt = datetime.strptime(snoozed_until_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                    is_snoozed = datetime.now(UTC) < snooze_dt
                except ValueError:
                    is_snoozed = False

            await db.execute(
                """UPDATE incidents
                   SET alert_count = alert_count + 1,
                       detail = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (detail, inc_id),
            )
            await db.commit()
            return inc_id, False, is_snoozed

        cursor = await db.execute(
            """INSERT INTO incidents (source, target, severity, title, detail)
               VALUES (?, ?, ?, ?, ?)""",
            (source, target, severity, title, detail),
        )
        await db.commit()
        return cursor.lastrowid, True, False  # type: ignore[return-value]


async def resolve_incident(source: str, target: str) -> int | None:
    async with db_conn() as db:
        cursor = await db.execute(
            "SELECT id FROM incidents WHERE source=? AND target=? AND resolved_at IS NULL LIMIT 1",
            (source, target),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        inc_id = row["id"]
        await db.execute(
            "UPDATE incidents SET resolved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (inc_id,),
        )
        await db.commit()
        return inc_id


async def resolve_incident_by_id(incident_id: int) -> bool:
    async with db_conn() as db:
        await db.execute(
            "UPDATE incidents SET resolved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (incident_id,),
        )
        await db.commit()
        return True


async def snooze_incident(incident_id: int, until_dt: datetime) -> bool:
    """Snooze incident until the given UTC datetime."""
    async with db_conn() as db:
        await db.execute(
            "UPDATE incidents SET snoozed_until=?, updated_at=datetime('now') WHERE id=?",
            (_to_sql_dt(until_dt), incident_id),
        )
        await db.commit()
        return True


async def list_open_incidents(limit: int = 20, offset: int = 0) -> list[dict]:
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT * FROM incidents
               WHERE resolved_at IS NULL
               ORDER BY severity DESC, updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def list_incidents(limit: int = 20, offset: int = 0) -> list[dict]:
    async with db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM incidents ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def count_open_incidents() -> int:
    async with db_conn() as db:
        cursor = await db.execute("SELECT COUNT(*) as c FROM incidents WHERE resolved_at IS NULL")
        row = await cursor.fetchone()
        return row["c"] if row else 0


async def prune_old_incidents(days: int = 30) -> int:
    async with db_conn() as db:
        cursor = await db.execute(
            """DELETE FROM incidents
               WHERE resolved_at IS NOT NULL
               AND resolved_at < datetime('now', ? || ' days')""",
            (f"-{days}",),
        )
        await db.commit()
        return cursor.rowcount  # type: ignore[return-value]


async def delete_incident_by_id(incident_id: int) -> bool:
    """Permanently delete an incident from the audit ledger."""
    async with db_conn() as db:
        cursor = await db.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
        await db.commit()
        return cursor.rowcount > 0
