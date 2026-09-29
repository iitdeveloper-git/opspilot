"""
CRUD operations for the renewals table.

Fix #4: snooze_renewal writes to snoozed_until column (not last_reminded_at).
        get_due_renewals filters out snoozed renewals.
Fix #5: mark_paid clamps days to last day of target month to avoid ValueError.
Fix #2: all datetimes use '%Y-%m-%d %H:%M:%S' UTC format for SQL comparability.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime

from opspilot.db.engine import db_conn


def _to_sql_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _advance_month(d: date) -> date:
    """Advance date by one month, clamping to last day of the target month."""
    month = d.month + 1
    year = d.year
    if month > 12:
        month, year = 1, year + 1
    last_day = calendar.monthrange(year, month)[1]
    return d.replace(year=year, month=month, day=min(d.day, last_day))


def _advance_year(d: date) -> date:
    """Advance date by one year, clamping Feb 29 → Feb 28 in non-leap years."""
    year = d.year + 1
    last_day = calendar.monthrange(year, d.month)[1]
    return d.replace(year=year, day=min(d.day, last_day))


async def seed_renewals_from_yaml(items: list[dict]) -> int:
    """Seed from YAML config. INSERT OR IGNORE prevents duplicates on restart."""
    if not items:
        return 0
    inserted = 0
    async with db_conn() as db:
        for item in items:
            try:
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO renewals
                       (name, category, due_date, amount, currency, notes,
                        recurrence, remind_days_before, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yaml_seed')""",
                    (
                        item["name"],
                        item.get("category", "other"),
                        item["due_date"],
                        item.get("amount"),
                        item.get("currency", "INR"),
                        item.get("notes", ""),
                        item.get("recurrence", "none"),
                        item.get("remind_days_before", 7),
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
            except Exception as exc:
                import logging

                logging.getLogger("opspilot.db.renewals").warning(f"Failed to seed renewal '{item.get('name')}': {exc}")
        await db.commit()
    return inserted


async def list_renewals(status: str | None = None) -> list[dict]:
    async with db_conn() as db:
        if status:
            cursor = await db.execute("SELECT * FROM renewals WHERE status=? ORDER BY due_date ASC", (status,))
        else:
            cursor = await db.execute("SELECT * FROM renewals ORDER BY due_date ASC")
        return [dict(r) for r in await cursor.fetchall()]


async def get_renewal(renewal_id: int) -> dict | None:
    async with db_conn() as db:
        cursor = await db.execute("SELECT * FROM renewals WHERE id=?", (renewal_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def add_renewal(
    name: str,
    category: str,
    due_date: str,
    amount: float | None = None,
    currency: str = "INR",
    notes: str = "",
    recurrence: str = "none",
    remind_days_before: int = 7,
) -> int:
    async with db_conn() as db:
        cursor = await db.execute(
            """INSERT INTO renewals
               (name, category, due_date, amount, currency, notes, recurrence, remind_days_before, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual')""",
            (name, category, due_date, amount, currency, notes, recurrence, remind_days_before),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def mark_paid(renewal_id: int) -> dict | None:
    """Mark renewal as paid. If recurring, inserts next renewal with correctly clamped date."""
    async with db_conn() as db:
        cursor = await db.execute("SELECT * FROM renewals WHERE id=?", (renewal_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        renewal = dict(row)

        await db.execute(
            "UPDATE renewals SET status='paid', paid_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (renewal_id,),
        )

        next_renewal: dict | None = None
        recurrence = renewal.get("recurrence", "none")
        if recurrence != "none":
            due = date.fromisoformat(renewal["due_date"])
            if recurrence == "monthly":
                next_due = _advance_month(due)
            elif recurrence == "yearly":
                next_due = _advance_year(due)
            else:
                # Unknown recurrence — skip roll-forward, don't silently break
                import logging

                logging.getLogger("opspilot.db.renewals").warning(
                    f"Unknown recurrence '{recurrence}' for renewal #{renewal_id}. Skipping roll-forward."
                )
                await db.commit()
                return None

            await db.execute(
                """INSERT OR IGNORE INTO renewals
                   (name, category, due_date, amount, currency, notes, recurrence, remind_days_before, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto_recur')""",
                (
                    renewal["name"],
                    renewal["category"],
                    next_due.isoformat(),
                    renewal["amount"],
                    renewal["currency"],
                    renewal["notes"],
                    recurrence,
                    renewal["remind_days_before"],
                ),
            )
            next_cursor = await db.execute(
                "SELECT * FROM renewals WHERE name=? AND due_date=?",
                (renewal["name"], next_due.isoformat()),
            )
            next_row = await next_cursor.fetchone()
            next_renewal = dict(next_row) if next_row else None

        await db.commit()
        return next_renewal


async def snooze_renewal(renewal_id: int, until_dt: datetime) -> bool:
    """
    Fix #4: writes to snoozed_until column (not last_reminded_at).
    The scheduler's get_due_renewals filters out rows where snoozed_until > now.
    """
    async with db_conn() as db:
        await db.execute(
            "UPDATE renewals SET snoozed_until=?, updated_at=datetime('now') WHERE id=?",
            (_to_sql_dt(until_dt), renewal_id),
        )
        await db.commit()
        return True


async def get_due_renewals() -> list[dict]:
    """
    Return pending renewals within their reminder window, excluding:
    - Renewals reminded today (last_reminded_at starts with today's UTC date)
    - Snoozed renewals (snoozed_until > now in UTC)
    """
    today_utc = datetime.now(UTC).strftime("%Y-%m-%d")
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT * FROM renewals
               WHERE status = 'pending'
               AND date(due_date, '-' || remind_days_before || ' days') <= date('now')
               AND (last_reminded_at IS NULL OR last_reminded_at NOT LIKE ?)
               AND (snoozed_until IS NULL OR snoozed_until <= datetime('now'))
               ORDER BY due_date ASC""",
            (f"{today_utc}%",),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def mark_reminded(renewal_id: int) -> None:
    async with db_conn() as db:
        await db.execute(
            "UPDATE renewals SET last_reminded_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (renewal_id,),
        )
        await db.commit()


async def delete_renewal(renewal_id: int) -> bool:
    """
    Soft-delete: marks status='cancelled' and sets paid_at so the
    UNIQUE(name, due_date, category) constraint prevents the YAML seed
    from re-inserting this renewal on the next restart.
    """
    async with db_conn() as db:
        await db.execute(
            "UPDATE renewals SET status='cancelled', updated_at=datetime('now') WHERE id=?",
            (renewal_id,),
        )
        await db.commit()
        return True


async def update_renewal(
    renewal_id: int,
    name: str,
    category: str,
    due_date: str,
    amount: float | None = None,
    currency: str = "INR",
    notes: str = "",
    recurrence: str = "none",
    remind_days_before: int = 7,
) -> bool:
    """Update an existing renewal entry."""
    async with db_conn() as db:
        await db.execute(
            """UPDATE renewals
               SET name=?, category=?, due_date=?, amount=?, currency=?, notes=?,
                   recurrence=?, remind_days_before=?, updated_at=datetime('now')
               WHERE id=?""",
            (name, category, due_date, amount, currency, notes, recurrence, remind_days_before, renewal_id),
        )
        await db.commit()
        return True
