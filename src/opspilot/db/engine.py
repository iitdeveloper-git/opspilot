"""
OpsPilot DB Engine — aiosqlite-backed SQLite.

Usage:
    async with db_conn() as db:
        await db.execute(...)
        await db.commit()
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger("opspilot.db")

_DB_PATH: Path = Path("data/opspilot.db")


def set_db_path(path: str | Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(path)


@asynccontextmanager
async def db_conn() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager yielding a configured aiosqlite connection."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(_DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn


async def init_db() -> None:
    """Create all tables if they don't exist. Idempotent — safe on every startup."""
    logger.info(f"Initialising OpsPilot DB at {_DB_PATH}")
    async with db_conn() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS renewals (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL,
                category            TEXT    NOT NULL DEFAULT 'other',
                due_date            TEXT    NOT NULL,
                amount              REAL,
                currency            TEXT    NOT NULL DEFAULT 'INR',
                notes               TEXT    NOT NULL DEFAULT '',
                status              TEXT    NOT NULL DEFAULT 'pending',
                recurrence          TEXT    NOT NULL DEFAULT 'none',
                remind_days_before  INTEGER NOT NULL DEFAULT 7,
                last_reminded_at    TEXT,
                snoozed_until       TEXT,
                paid_at             TEXT,
                paid_by             TEXT    NOT NULL DEFAULT '',
                source              TEXT    NOT NULL DEFAULT 'manual',
                created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, due_date, category)
            );
            CREATE TABLE IF NOT EXISTS endpoints (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL UNIQUE,
                url             TEXT    NOT NULL,
                expected_status INTEGER NOT NULL DEFAULT 200,
                timeout_seconds INTEGER NOT NULL DEFAULT 5,
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS incidents (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source        TEXT    NOT NULL,
                target        TEXT    NOT NULL,
                severity      TEXT    NOT NULL DEFAULT 'warning',
                title         TEXT    NOT NULL,
                detail        TEXT    NOT NULL DEFAULT '',
                resolved_at   TEXT,
                snoozed_until TEXT,
                alert_count   INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS container_snooze (
                container_name TEXT    PRIMARY KEY,
                expires_at     TEXT,
                snoozed_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS alert_routes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT    NOT NULL,
                chat_id     TEXT    NOT NULL,
                categories  TEXT    NOT NULL DEFAULT '["all"]',
                events      TEXT    NOT NULL DEFAULT '[]',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS ssl_domains (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                domain                TEXT    NOT NULL UNIQUE,
                port                  INTEGER NOT NULL DEFAULT 443,
                issuer                TEXT    NOT NULL DEFAULT '',
                expires_at            TEXT    NOT NULL DEFAULT '',
                days_remaining        INTEGER NOT NULL DEFAULT 0,
                is_valid              INTEGER NOT NULL DEFAULT 1,
                last_checked_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                error                 TEXT    NOT NULL DEFAULT '',
                registrar             TEXT    NOT NULL DEFAULT '',
                domain_expires_at     TEXT    NOT NULL DEFAULT '',
                domain_days_remaining INTEGER NOT NULL DEFAULT 0,
                registration_date     TEXT    NOT NULL DEFAULT '',
                created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
        # Migration: add snoozed_until to renewals if upgrading from v0.2
        try:
            await db.execute("ALTER TABLE renewals ADD COLUMN snoozed_until TEXT")
            await db.commit()
            logger.info("Migration: added snoozed_until to renewals table.")
        except Exception:
            pass  # Column already exists — normal on fresh installs

        # Migration: add paid_by to renewals
        try:
            await db.execute("ALTER TABLE renewals ADD COLUMN paid_by TEXT NOT NULL DEFAULT ''")
            await db.commit()
            logger.info("Migration: added paid_by to renewals table.")
        except Exception:
            pass

        # Migration: add domain governance columns to ssl_domains
        for col_def in [
            ("registrar", "TEXT NOT NULL DEFAULT ''"),
            ("domain_expires_at", "TEXT NOT NULL DEFAULT ''"),
            ("domain_days_remaining", "INTEGER NOT NULL DEFAULT 0"),
            ("registration_date", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE ssl_domains ADD COLUMN {col_def[0]} {col_def[1]}")
                await db.commit()
                logger.info(f"Migration: added {col_def[0]} to ssl_domains table.")
            except Exception:
                pass
    logger.info("OpsPilot DB ready.")
