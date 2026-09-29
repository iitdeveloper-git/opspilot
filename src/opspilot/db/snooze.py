"""
DB-backed container snooze.

All datetimes stored as '%Y-%m-%d %H:%M:%S' (UTC, no timezone suffix)
so they compare correctly with SQLite's datetime('now') which returns the same format.
"""
from __future__ import annotations

from datetime import UTC, datetime

from opspilot.db.engine import db_conn


def _to_sql_dt(dt: datetime) -> str:
    """Convert a UTC-aware datetime to the SQL-comparable string format."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


async def snooze_container(name: str, expires_at: datetime | None) -> None:
    async with db_conn() as db:
        await db.execute(
            """INSERT INTO container_snooze (container_name, expires_at)
               VALUES (?, ?)
               ON CONFLICT(container_name) DO UPDATE
               SET expires_at=excluded.expires_at, snoozed_at=datetime('now')""",
            (name, _to_sql_dt(expires_at) if expires_at else None),
        )
        await db.commit()


async def unsnooze_container(name: str) -> None:
    async with db_conn() as db:
        await db.execute("DELETE FROM container_snooze WHERE container_name = ?", (name,))
        await db.commit()


async def is_snoozed(name: str) -> bool:
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT container_name FROM container_snooze
               WHERE container_name = ?
               AND (expires_at IS NULL OR expires_at > datetime('now'))""",
            (name,),
        )
        return await cursor.fetchone() is not None


async def cleanup_expired_snoozes() -> int:
    async with db_conn() as db:
        cursor = await db.execute(
            "DELETE FROM container_snooze WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
        )
        await db.commit()
        return cursor.rowcount  # type: ignore[return-value]


async def list_snoozed() -> list[dict]:
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT container_name, expires_at, snoozed_at FROM container_snooze
               WHERE expires_at IS NULL OR expires_at > datetime('now')
               ORDER BY container_name"""
        )
        rows = await cursor.fetchall()
        result = []
        now = datetime.now(UTC)
        for r in rows:
            exp = r["expires_at"]
            if exp:
                try:
                    exp_dt = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                    diff = exp_dt - now
                    total_secs = int(diff.total_seconds())
                    hrs, rem = divmod(max(total_secs, 0), 3600)
                    mins = rem // 60
                    remaining = f"{hrs}h {mins}m left" if hrs > 0 else f"{mins}m left"
                except ValueError:
                    remaining = "active"
            else:
                remaining = "Indefinite"
            result.append({"name": r["container_name"], "expires_at": exp, "remaining": remaining})
        return result
