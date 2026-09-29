"""
Alert routing database operations.
Allows configuring custom Telegram Chat IDs per alert category and event/target filter.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opspilot.db.engine import db_conn

logger = logging.getLogger("opspilot.db.alert_routes")


async def create_route(
    label: str,
    chat_id: str,
    categories: list[str] | None = None,
    events: list[str] | None = None,
    enabled: bool = True,
) -> int:
    """Create a new alert routing rule."""
    cat_list = categories or ["all"]
    ev_list = events or []
    async with db_conn() as db:
        cursor = await db.execute(
            """
            INSERT INTO alert_routes (label, chat_id, categories, events, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                label.strip(),
                chat_id.strip(),
                json.dumps(cat_list),
                json.dumps([e.strip().lower() for e in ev_list if e.strip()]),
                1 if enabled else 0,
            ),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def list_routes() -> list[dict[str, Any]]:
    """List all configured alert routes."""
    async with db_conn() as db, db.execute(
        "SELECT id, label, chat_id, categories, events, enabled, created_at, updated_at FROM alert_routes ORDER BY id ASC"
    ) as cursor:
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                try:
                    cats = json.loads(r["categories"])
                except Exception:
                    cats = ["all"]
                try:
                    evs = json.loads(r["events"])
                except Exception:
                    evs = []
                result.append(
                    {
                        "id": r["id"],
                        "label": r["label"],
                        "chat_id": r["chat_id"],
                        "categories": cats,
                        "events": evs,
                        "enabled": bool(r["enabled"]),
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                )
            return result


async def get_route(route_id: int) -> dict[str, Any] | None:
    """Get a single route by ID."""
    async with db_conn() as db, db.execute(
        "SELECT id, label, chat_id, categories, events, enabled, created_at, updated_at FROM alert_routes WHERE id = ?",
        (route_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        try:
            cats = json.loads(row["categories"])
        except Exception:
            cats = ["all"]
        try:
            evs = json.loads(row["events"])
        except Exception:
            evs = []
        return {
            "id": row["id"],
            "label": row["label"],
            "chat_id": row["chat_id"],
            "categories": cats,
            "events": evs,
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


async def update_route(
    route_id: int,
    label: str,
    chat_id: str,
    categories: list[str] | None = None,
    events: list[str] | None = None,
    enabled: bool = True,
) -> bool:
    """Update an existing route rule."""
    cat_list = categories or ["all"]
    ev_list = events or []
    async with db_conn() as db:
        cursor = await db.execute(
            """
            UPDATE alert_routes
            SET label = ?, chat_id = ?, categories = ?, events = ?, enabled = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                label.strip(),
                chat_id.strip(),
                json.dumps(cat_list),
                json.dumps([e.strip().lower() for e in ev_list if e.strip()]),
                1 if enabled else 0,
                route_id,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_route(route_id: int) -> bool:
    """Delete an alert route rule."""
    async with db_conn() as db:
        cursor = await db.execute(
            "DELETE FROM alert_routes WHERE id = ?",
            (route_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_matching_chat_ids(
    category: str,
    target: str = "",
    event: str = "",
    default_chat_id: str = "",
) -> list[str]:
    """
    Find all enabled Chat IDs configured to receive an alert of given category and target/event.
    If no custom route matches, returns [default_chat_id] (if configured).
    """
    routes = await list_routes()
    active_routes = [r for r in routes if r["enabled"]]
    if not active_routes:
        return [default_chat_id] if default_chat_id else []

    target_lower = target.lower()
    event_lower = event.lower()

    matched_chat_ids: list[str] = []

    for r in active_routes:
        cats = r["categories"]
        # Category check: does this route accept this category or 'all'?
        if "all" not in cats and category not in cats:
            continue

        # Event / Target filter check:
        # If events list is empty or contains "*", it matches anything in the category.
        evs = r["events"]
        if not evs or "*" in evs:
            if r["chat_id"] not in matched_chat_ids:
                matched_chat_ids.append(r["chat_id"])
            continue

        # Check if any filter item matches target or event name
        matches = False
        for ev in evs:
            if ev and (ev in target_lower or ev in event_lower):
                matches = True
                break

        if matches and r["chat_id"] not in matched_chat_ids:
            matched_chat_ids.append(r["chat_id"])

    # Fallback to default if no route matched
    if not matched_chat_ids and default_chat_id:
        return [default_chat_id]

    return matched_chat_ids
