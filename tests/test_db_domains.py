"""Unit tests for SSL & Domain tracking database operations."""

import pytest

from opspilot.db.domains import (
    add_domain,
    delete_domain,
    get_domain,
    list_domains,
    recheck_domain,
    seed_domains,
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
