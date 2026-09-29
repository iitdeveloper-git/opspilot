"""Key-value settings store backed by DB. Hot-configurable without redeploy."""

from __future__ import annotations

from opspilot.db.engine import db_conn


async def get_setting(key: str, default: str = "") -> str:
    async with db_conn() as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    async with db_conn() as db:
        await db.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value),
        )
        await db.commit()


async def get_all_settings() -> dict[str, str]:
    async with db_conn() as db:
        cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
        rows = await cursor.fetchall()
        return {r["key"]: r["value"] for r in rows}
