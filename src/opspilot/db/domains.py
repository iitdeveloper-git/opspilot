"""Database access layer for tracked SSL domains and domain registration governance."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from opspilot.db.engine import db_conn
from opspilot.monitor.ssl import check_domain_rdap, check_domain_ssl

logger = logging.getLogger("opspilot.db.domains")


def _calc_domain_days(expires_at_str: str) -> int:
    """Calculate days remaining from a YYYY-MM-DD or ISO date string."""
    if not expires_at_str:
        return 0
    try:
        clean_date = expires_at_str[:10]
        dt = datetime.strptime(clean_date, "%Y-%m-%d").replace(tzinfo=UTC)
        days = (dt - datetime.now(UTC)).days
        return max(0, days)
    except Exception:
        return 0


async def add_domain(
    domain: str,
    port: int = 443,
    registrar: str | None = None,
    domain_expires_at: str | None = None,
) -> dict[str, Any]:
    """Add a domain, inspect SSL and ICANN RDAP registration, and persist the record."""
    clean_domain = domain.strip().lower()
    if clean_domain.startswith("https://"):
        clean_domain = clean_domain[8:]
    if clean_domain.startswith("http://"):
        clean_domain = clean_domain[7:]
    clean_domain = clean_domain.split("/")[0].split(":")[0]

    # 1. Perform live SSL certificate inspection
    ssl_status = check_domain_ssl(clean_domain, port=port)

    # 2. Discover or calculate domain registration expiration
    reg_val = registrar or ""
    dom_exp_val = domain_expires_at or ""
    dom_days_val = _calc_domain_days(dom_exp_val)
    registration_date_val = ""

    # If domain expiration or registrar not explicitly passed, query ICANN RDAP
    if not dom_exp_val or not reg_val:
        rdap_info = check_domain_rdap(clean_domain)
        if not reg_val and rdap_info.get("registrar"):
            reg_val = rdap_info["registrar"]
        if not dom_exp_val and rdap_info.get("domain_expires_at"):
            dom_exp_val = rdap_info["domain_expires_at"]
            dom_days_val = rdap_info.get("domain_days_remaining", _calc_domain_days(dom_exp_val))
        if rdap_info.get("registration_date"):
            registration_date_val = rdap_info["registration_date"]

    async with db_conn() as db:
        cursor = await db.execute(
            """INSERT INTO ssl_domains (
                   domain, port, issuer, expires_at, days_remaining, is_valid,
                   last_checked_at, error, registrar, domain_expires_at,
                   domain_days_remaining, registration_date
               )
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                   port = excluded.port,
                   issuer = excluded.issuer,
                   expires_at = excluded.expires_at,
                   days_remaining = excluded.days_remaining,
                   is_valid = excluded.is_valid,
                   last_checked_at = excluded.last_checked_at,
                   error = excluded.error,
                   registrar = CASE WHEN excluded.registrar != '' THEN excluded.registrar ELSE ssl_domains.registrar END,
                   domain_expires_at = CASE WHEN excluded.domain_expires_at != '' THEN excluded.domain_expires_at ELSE ssl_domains.domain_expires_at END,
                   domain_days_remaining = CASE WHEN excluded.domain_days_remaining > 0 THEN excluded.domain_days_remaining ELSE ssl_domains.domain_days_remaining END,
                   registration_date = CASE WHEN excluded.registration_date != '' THEN excluded.registration_date ELSE ssl_domains.registration_date END
               RETURNING id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error,
                         registrar, domain_expires_at, domain_days_remaining, registration_date, created_at""",
            (
                clean_domain,
                port,
                ssl_status.issuer,
                ssl_status.expires_at,
                ssl_status.days_remaining,
                1 if ssl_status.is_valid else 0,
                ssl_status.error or "",
                reg_val,
                dom_exp_val,
                dom_days_val,
                registration_date_val,
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
        "registrar": row[9] or "",
        "domain_expires_at": row[10] or "",
        "domain_days_remaining": row[11] or 0,
        "registration_date": row[12] or "",
        "created_at": row[13],
    }


async def list_domains() -> list[dict[str, Any]]:
    """List all tracked domains ordered by expiration days ascending."""
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error,
                      registrar, domain_expires_at, domain_days_remaining, registration_date, created_at
               FROM ssl_domains
               ORDER BY is_valid ASC, days_remaining ASC, domain ASC"""
        )
        rows = await cursor.fetchall()

    result = []
    for r in rows:
        exp_at = r[4] or ""
        ssl_days = _calc_domain_days(exp_at) if exp_at else (r[5] or 0)
        dom_exp_at = r[10] or ""
        dom_days = _calc_domain_days(dom_exp_at) if dom_exp_at else (r[11] or 0)
        result.append(
            {
                "id": r[0],
                "domain": r[1],
                "port": r[2],
                "issuer": r[3],
                "expires_at": exp_at,
                "days_remaining": ssl_days,
                "is_valid": bool(r[6]),
                "last_checked_at": r[7],
                "error": r[8],
                "registrar": r[9] or "",
                "domain_expires_at": dom_exp_at,
                "domain_days_remaining": dom_days,
                "registration_date": r[12] or "",
                "created_at": r[13],
            }
        )
    return result


async def get_domain(domain_id: int) -> dict[str, Any] | None:
    """Retrieve a single tracked domain by ID."""
    async with db_conn() as db:
        cursor = await db.execute(
            """SELECT id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error,
                      registrar, domain_expires_at, domain_days_remaining, registration_date, created_at
               FROM ssl_domains
               WHERE id = ?""",
            (domain_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None

    exp_at = row[4] or ""
    ssl_days = _calc_domain_days(exp_at) if exp_at else (row[5] or 0)
    dom_exp_at = row[10] or ""
    dom_days = _calc_domain_days(dom_exp_at) if dom_exp_at else (row[11] or 0)

    return {
        "id": row[0],
        "domain": row[1],
        "port": row[2],
        "issuer": row[3],
        "expires_at": exp_at,
        "days_remaining": ssl_days,
        "is_valid": bool(row[6]),
        "last_checked_at": row[7],
        "error": row[8],
        "registrar": row[9] or "",
        "domain_expires_at": dom_exp_at,
        "domain_days_remaining": dom_days,
        "registration_date": row[12] or "",
        "created_at": row[13],
    }


async def update_domain_governance(
    domain_id: int,
    port: int = 443,
    registrar: str = "",
    domain_expires_at: str = "",
) -> dict[str, Any] | None:
    """Update domain port, registrar, and domain renewal expiry date."""
    dom_days = _calc_domain_days(domain_expires_at)
    async with db_conn() as db:
        cursor = await db.execute(
            """UPDATE ssl_domains
               SET port = ?, registrar = ?, domain_expires_at = ?, domain_days_remaining = ?
               WHERE id = ?
               RETURNING id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error,
                         registrar, domain_expires_at, domain_days_remaining, registration_date, created_at""",
            (port, registrar, domain_expires_at, dom_days, domain_id),
        )
        row = await cursor.fetchone()
        await db.commit()

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
        "registrar": row[9] or "",
        "domain_expires_at": row[10] or "",
        "domain_days_remaining": row[11] or 0,
        "registration_date": row[12] or "",
        "created_at": row[13],
    }


async def recheck_domain(domain_id: int) -> dict[str, Any] | None:
    """Perform a live SSL certificate and RDAP recheck for a specific domain ID."""
    async with db_conn() as db:
        cursor = await db.execute(
            "SELECT domain, port, registrar, domain_expires_at FROM ssl_domains WHERE id = ?", (domain_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        domain, port, current_reg, current_dom_exp = row[0], row[1], row[2], row[3]

    # Recheck SSL
    ssl_status = check_domain_ssl(domain, port=port)

    # Recheck RDAP if domain_expires_at is missing or refresh
    new_reg = current_reg
    new_dom_exp = current_dom_exp
    new_dom_days = _calc_domain_days(current_dom_exp)
    new_reg_date = ""

    rdap_info = check_domain_rdap(domain)
    if rdap_info.get("domain_expires_at"):
        new_dom_exp = rdap_info["domain_expires_at"]
        new_dom_days = rdap_info.get("domain_days_remaining", _calc_domain_days(new_dom_exp))
    if rdap_info.get("registrar") and not new_reg:
        new_reg = rdap_info["registrar"]
    if rdap_info.get("registration_date"):
        new_reg_date = rdap_info["registration_date"]

    async with db_conn() as db:
        cursor = await db.execute(
            """UPDATE ssl_domains
               SET issuer = ?, expires_at = ?, days_remaining = ?, is_valid = ?,
                   last_checked_at = datetime('now'), error = ?,
                   registrar = ?, domain_expires_at = ?, domain_days_remaining = ?,
                   registration_date = CASE WHEN ? != '' THEN ? ELSE registration_date END
               WHERE id = ?
               RETURNING id, domain, port, issuer, expires_at, days_remaining, is_valid, last_checked_at, error,
                         registrar, domain_expires_at, domain_days_remaining, registration_date, created_at""",
            (
                ssl_status.issuer,
                ssl_status.expires_at,
                ssl_status.days_remaining,
                1 if ssl_status.is_valid else 0,
                ssl_status.error or "",
                new_reg,
                new_dom_exp,
                new_dom_days,
                new_reg_date,
                new_reg_date,
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
        "registrar": updated_row[9] or "",
        "domain_expires_at": updated_row[10] or "",
        "domain_days_remaining": updated_row[11] or 0,
        "registration_date": updated_row[12] or "",
        "created_at": updated_row[13],
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
