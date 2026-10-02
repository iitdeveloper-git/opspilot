"""Unit tests for SSL & Domain tracking database operations."""

import pytest

from opspilot.db.domains import (
    _calc_domain_days,
    add_domain,
    delete_domain,
    get_domain,
    list_domains,
    recheck_domain,
    seed_domains,
    update_domain_governance,
)
from opspilot.db.engine import init_db, set_db_path
from opspilot.monitor.ssl import SSLStatus


@pytest.fixture(autouse=True)
async def test_db(tmp_path):
    db_file = tmp_path / "test_domains.db"
    set_db_path(db_file)
    await init_db()
    yield
    set_db_path("data/opspilot.db")


@pytest.mark.asyncio
async def test_add_and_list_domains(monkeypatch):
    def mock_check(domain: str, port: int = 443, timeout: int = 5):
        return SSLStatus(
            domain=domain,
            is_valid=True,
            days_remaining=88,
            expires_at="2026-04-01",
            issuer="Let's Encrypt Authority",
            error=None,
        )

    from opspilot.db import domains

    monkeypatch.setattr(domains, "check_domain_ssl", mock_check)

    # 1. Add domain
    dom = await add_domain("example.com", port=443)
    assert dom["domain"] == "example.com"
    assert dom["is_valid"] is True
    assert dom["days_remaining"] == 88
    assert dom["issuer"] == "Let's Encrypt Authority"

    # 2. List domains
    all_doms = await list_domains()
    assert len(all_doms) == 1
    assert all_doms[0]["domain"] == "example.com"

    # 3. Get domain
    fetched = await get_domain(dom["id"])
    assert fetched is not None
    assert fetched["domain"] == "example.com"

    # 4. Recheck domain
    rechecked = await recheck_domain(dom["id"])
    assert rechecked is not None
    assert rechecked["domain"] == "example.com"

    # 5. Delete domain
    deleted = await delete_domain(dom["id"])
    assert deleted is True

    # 6. Confirm gone
    assert await get_domain(dom["id"]) is None


@pytest.mark.asyncio
async def test_seed_domains(monkeypatch):
    def mock_check(domain: str, port: int = 443, timeout: int = 5):
        return SSLStatus(
            domain=domain,
            is_valid=True,
            days_remaining=60,
            expires_at="2026-03-01",
            issuer="Cloudflare Inc",
            error=None,
        )

    from opspilot.db import domains

    monkeypatch.setattr(domains, "check_domain_ssl", mock_check)

    count = await seed_domains(["seed1.com", "seed2.com"])
    assert count == 2
    all_doms = await list_domains()
    assert len(all_doms) == 2


@pytest.mark.asyncio
async def test_domain_governance_rdap_and_manual_update(monkeypatch):
    from opspilot.db import domains

    def mock_ssl(domain: str, port: int = 443, timeout: int = 5):
        return SSLStatus(
            domain=domain,
            is_valid=True,
            days_remaining=85,
            expires_at="2026-11-01",
            issuer="Google Trust Services",
            error=None,
        )

    def mock_rdap(domain: str, timeout: int = 5):
        return {
            "domain": domain,
            "registrar": "GoDaddy.com, LLC",
            "domain_expires_at": "2027-02-15",
            "domain_days_remaining": 365,
            "registration_date": "2020-02-15",
        }

    monkeypatch.setattr(domains, "check_domain_ssl", mock_ssl)
    monkeypatch.setattr(domains, "check_domain_rdap", mock_rdap)

    # 1. Add domain with RDAP discovery
    record = await add_domain("iitdeveloper.com")
    assert record["domain"] == "iitdeveloper.com"
    assert record["registrar"] == "GoDaddy.com, LLC"
    assert record["domain_expires_at"] == "2027-02-15"
    assert record["days_remaining"] > 0
    assert record["domain_days_remaining"] > 0

    # 2. Update governance manually
    updated = await update_domain_governance(
        domain_id=record["id"],
        port=8443,
        registrar="Cloudflare Registrar",
        domain_expires_at="2028-05-20",
    )
    assert updated is not None
    assert updated["port"] == 8443
    assert updated["registrar"] == "Cloudflare Registrar"
    assert updated["domain_expires_at"] == "2028-05-20"

    # 3. Test _calc_domain_days
    assert _calc_domain_days("") == 0
    assert _calc_domain_days("invalid-date") == 0
    assert _calc_domain_days("2099-01-01") > 1000

