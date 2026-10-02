"""Database access layer for tracked SSL domains and certificates."""

from __future__ import annotations

import logging
from typing import Any

from opspilot.db.engine import db_conn
from opspilot.monitor.ssl import check_domain_ssl

logger = logging.getLogger("opspilot.db.domains")


async def add_domain(domain: str, port: int = 443) -> dict[str, Any]:
    """Add a domain, immediately check its SSL certificate, and persist the record."""
    clean_domain = domain.strip().lower()
    if clean_domain.startswith("https://"):
        clean_domain = clean_domain[8:]
    if clean_domain.startswith("http://"):
        clean_domain = clean_domain[7:]
    clean_domain = clean_domain.split("/")[0].split(":")[0]

    # Perform live SSL certificate inspection
    ssl_status = check_domain_ssl(clean_domain, port=port)

    async with db_conn() as db:
        cursor = await db.execute(
            """INSERT INTO ssl_domains (domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)
               ON CONFLICT(domain) DO UPDATE SET
                   port = excluded.port,
                   issuer = excluded.issuer,
                   expires_at = excluded.expires_at,
                   days_remaining = excluded.days_remaining,
                   is_valid = excluded.is_valid,
                   last_checked_at = excluded.last_checked_at,
                   error = excluded.error
               RETURNING id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error, created_at""",
            (
                clean_domain,
                port,
                ssl_status.issuer,
                ssl_status.expires_at,
                ssl_status.days_remaining,
                1 if ssl_status.is_valid else 0,
                ssl_status.error or "",
            ),
        )
        row = await cursor.fetchone()
        await db.commit()

    if not row:
        raise ValueError(f"Could not insert or update domain '{clean_domain}'")

    return {
        "id": row[0],
        "domain": row[1],
        "port": row[2],
        "issuer": row[3],
        "expires_at": row[4],
        "days_remaining": row[5],
        "is_valid": bool(row[6]),
        "last_checked_at": row[7],
        "error": row[8],
        "created_at": row[9],
    }


async def list_domains() -> list[dict[str, Any]]:
    """List all tracked domains ordered by expiration days ascending."""
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error, created_at
               FROM ssl_domains
               ORDER BY is_valid ASC, days_remaining ASC, domain ASC"""
        )
        rows = await cursor.fetchall()

    return [
        {
            "id": r[0],
            "domain": r[1],
            "port": r[2],
            "issuer": r[3],
            "expires_at": r[4],
            "days_remaining": r[5],
            "is_valid": bool(r[6]),
            "last_checked_at": r[7],
            "error": r[8],
            "created_at": r[9],
        }
        for r in rows
    ]


async def get_domain(domain_id: int) -> dict[str, Any] | None:
    """Retrieve a single tracked domain by ID."""
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error, created_at
               FROM ssl_domains
               WHERE id = ?""",
            (domain_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "domain": row[1],
        "port": row[2],
        "issuer": row[3],
        "expires_at": row[4],
        "days_remaining": row[5],
        "is_valid": bool(row[6]),
        "last_checked_at": row[7],
        "error": row[8],
        "created_at": row[9],
    }


async def recheck_domain(domain_id: int) -> dict[str, Any] | None:
    """Perform a live SSL certificate recheck for a specific domain ID."""
    async with db_conn() as db:
        cursor = await db.execute("SELECT domain, port FROM ssl_domains WHERE id = ?", (domain_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        domain, port = row[0], row[1]

    ssl_status = check_domain_ssl(domain, port=port)

    async with db_conn() as db:
        cursor = await db.execute(
            """UPDATE ssl_domains
               SET issuer = ?, expires_at = ?, days_remaining = ?, is_valid = ?, last_checked_at = datetime('now'), error = ?
               WHERE id = ?
               RETURNING id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error, created_at""",
            (
                ssl_status.issuer,
                ssl_status.expires_at,
                ssl_status.days_remaining,
                1 if ssl_status.is_valid else 0,
                ssl_status.error or "",
                domain_id,
            ),
        )
        updated_row = await cursor.fetchone()
        await db.commit()

    if not updated_row:
        return None

    return {
        "id": updated_row[0],
        "domain": updated_row[1],
        "port": updated_row[2],
        "issuer": updated_row[3],
        "expires_at": updated_row[4],
        "days_remaining": updated_row[5],
        "is_valid": bool(updated_row[6]),
        "last_checked_at": updated_row[7],
        "error": updated_row[8],
        "created_at": updated_row[9],
    }


async def delete_domain(domain_id: int) -> bool:
    """Remove a domain from active tracking."""
    async with db_conn() as db:
        cursor = await db.execute("DELETE FROM ssl_domains WHERE id = ?", (domain_id,))
        await db.commit()
        return cursor.rowcount > 0


async def seed_domains(domains: list[str]) -> int:
    """Idempotently seed default domain targets."""
    if not domains:
        return 0
    seeded = 0
    for d in domains:
        try:
            await add_domain(d)
            seeded += 1
        except Exception as e:
            logger.warning(f"Could not seed domain {d}: {e}")
    return seeded
