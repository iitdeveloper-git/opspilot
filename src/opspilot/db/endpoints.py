"""CRUD for DB-managed HTTP endpoints — hot-configurable without redeploy."""
from __future__ import annotations

from opspilot.db.engine import db_conn


async def seed_endpoints_from_yaml(items: list[dict]) -> int:
    """Seed from YAML config. INSERT OR IGNORE prevents duplicates on restart."""
    if not items:
        return 0
    inserted = 0
    async with db_conn() as db:
        for item in items:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO endpoints (name, url, expected_status, timeout_seconds)
                   VALUES (?, ?, ?, ?)""",
                (
                    item["name"],
                    item["url"],
                    item.get("expected_status", 200),
                    item.get("timeout_seconds", 5),
                ),
            )
            if cursor.rowcount == 1:
                inserted += 1
        await db.commit()
    return inserted


async def list_endpoints(enabled_only: bool = True) -> list[dict]:
    async with db_conn() as db:
        if enabled_only:
            cursor = await db.execute(
                "SELECT * FROM endpoints WHERE enabled = 1 ORDER BY name ASC"
            )
        else:
            cursor = await db.execute("SELECT * FROM endpoints ORDER BY name ASC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_endpoint(
    name: str,
    url: str,
    expected_status: int = 200,
    timeout_seconds: int = 5,
) -> int:
    async with db_conn() as db:
        cursor = await db.execute(
            """INSERT INTO endpoints (name, url, expected_status, timeout_seconds)
               VALUES (?, ?, ?, ?)""",
            (name, url, expected_status, timeout_seconds),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def remove_endpoint(endpoint_id: int) -> bool:
    """
    Soft-delete: sets enabled=0 so the UNIQUE(name) constraint prevents
    the YAML seed from re-inserting this endpoint on the next restart.
    Hard-delete would let the seed bring it back.
    """
    async with db_conn() as db:
        await db.execute("UPDATE endpoints SET enabled=0 WHERE id=?", (endpoint_id,))
        await db.commit()
        return True


async def toggle_endpoint(endpoint_id: int, enabled: bool) -> bool:
    async with db_conn() as db:
        await db.execute(
            "UPDATE endpoints SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, endpoint_id),
        )
        await db.commit()
        return True
