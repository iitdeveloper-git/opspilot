"""Tests for db/renewals.py — covering Fix #4 (snooze), Fix #5 (date clamping), seed idempotency."""

from datetime import UTC, datetime, timedelta

import pytest

from opspilot.db.engine import init_db, set_db_path
from opspilot.db.renewals import (
    add_renewal,
    get_due_renewals,
    get_renewal,
    list_renewals,
    mark_paid,
    mark_reminded,
    seed_renewals_from_yaml,
    snooze_renewal,
    update_renewal,
)


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    await init_db()


# ─── Seed idempotency ─────────────────────────────────────────────────────────


async def test_seed_idempotent():
    items = [{"name": "OVH VPS", "category": "vps", "due_date": "2026-10-01"}]
    n1 = await seed_renewals_from_yaml(items)
    n2 = await seed_renewals_from_yaml(items)
    assert n1 == 1
    assert n2 == 0, "Second seed should not insert duplicate"
    rows = await list_renewals()
    assert len(rows) == 1


# ─── Mark paid + recurrence ───────────────────────────────────────────────────


async def test_mark_paid_monthly_normal():
    rid = await add_renewal("VPS", "vps", "2026-10-15", recurrence="monthly")
    next_r = await mark_paid(rid)
    assert next_r is not None
    assert next_r["due_date"] == "2026-11-15"
    paid = await get_renewal(rid)
    assert paid["status"] == "paid"


async def test_mark_paid_monthly_jan31_clamps_to_feb28():
    """Jan 31 monthly → Feb 28 (not Feb 31 which is invalid)."""
    rid = await add_renewal("VPS", "vps", "2026-01-31", recurrence="monthly")
    next_r = await mark_paid(rid)
    assert next_r is not None
    assert next_r["due_date"] == "2026-02-28"


async def test_mark_paid_monthly_dec31_rolls_to_jan():
    rid = await add_renewal("VPS", "vps", "2026-12-31", recurrence="monthly")
    next_r = await mark_paid(rid)
    assert next_r is not None
    assert next_r["due_date"] == "2027-01-31"


async def test_mark_paid_yearly_feb29_clamps():
    """Feb 29 2024 yearly → Feb 28 2025 (2025 is not a leap year)."""
    rid = await add_renewal("Domain", "domain", "2024-02-29", recurrence="yearly")
    next_r = await mark_paid(rid)
    assert next_r is not None
    assert next_r["due_date"] == "2025-02-28"


async def test_mark_paid_no_recurrence():
    rid = await add_renewal("One-off", "other", "2026-10-01", recurrence="none")
    next_r = await mark_paid(rid)
    assert next_r is None
    paid = await get_renewal(rid)
    assert paid["status"] == "paid"


# ─── Fix #4: snooze_renewal actually suppresses reminders ────────────────────


async def test_snooze_renewal_suppresses_reminders():
    """A snoozed renewal must NOT appear in get_due_renewals."""
    rid = await add_renewal("Domain", "domain", "2026-10-01", remind_days_before=30)
    # Confirm it's due without snooze
    due = await get_due_renewals()
    assert any(r["id"] == rid for r in due), "Should be due before snooze"

    # Snooze for 3 days
    until = datetime.now(UTC) + timedelta(days=3)
    await snooze_renewal(rid, until)

    due_after = await get_due_renewals()
    assert not any(r["id"] == rid for r in due_after), "Should NOT appear while snoozed"


async def test_snooze_renewal_reappears_after_expiry(monkeypatch):
    """A renewal should reappear after its snooze window has passed."""
    rid = await add_renewal("Domain", "domain", "2026-10-01", remind_days_before=30)
    # Snooze expires in the past (already expired)
    already_expired = datetime.now(UTC) - timedelta(hours=1)
    await snooze_renewal(rid, already_expired)

    due = await get_due_renewals()
    assert any(r["id"] == rid for r in due), "Should reappear after snooze expired"


# ─── last_reminded_at dedupe (one alert per day) ─────────────────────────────


async def test_mark_reminded_suppresses_same_day():
    rid = await add_renewal("VPS", "vps", "2026-10-01", remind_days_before=30)
    due = await get_due_renewals()
    assert any(r["id"] == rid for r in due)

    await mark_reminded(rid)
    due_again = await get_due_renewals()
    assert not any(r["id"] == rid for r in due_again), "Already reminded today"


# ─── paid_by & Billing KPIs ───────────────────────────────────────────────────


async def test_paid_by_persistence_and_update():
    rid = await add_renewal("OVH VPS", "vps", "2026-10-15", amount=4000.0, paid_by="Ravi")
    ren = await get_renewal(rid)
    assert ren is not None
    assert ren["paid_by"] == "Ravi"

    await update_renewal(rid, "OVH VPS", "vps", "2026-10-15", amount=4200.0, paid_by="Company")
    ren_updated = await get_renewal(rid)
    assert ren_updated is not None
    assert ren_updated["paid_by"] == "Company"
    assert ren_updated["amount"] == 4200.0


async def test_mark_paid_with_payer_and_recurrence_copy():
    rid = await add_renewal("OpenAI API", "saas", "2026-10-15", amount=1200.0, recurrence="monthly", paid_by="Ravi")
    next_r = await mark_paid(rid, paid_by="Company Account")
    assert next_r is not None
    assert next_r["paid_by"] == "Company Account"

    paid_r = await get_renewal(rid)
    assert paid_r is not None
    assert paid_r["status"] == "paid"
    assert paid_r["paid_by"] == "Company Account"
    assert paid_r["paid_at"] is not None


async def test_get_billing_kpis():
    from opspilot.db.renewals import get_billing_kpis

    await add_renewal("Monthly VPS", "vps", "2026-10-15", amount=3000.0, recurrence="monthly", paid_by="Ravi")
    await add_renewal("Yearly Domain", "domain", "2026-12-01", amount=1200.0, recurrence="yearly", paid_by="Company")
    kpis = await get_billing_kpis()
    assert kpis["currency"] == "INR"
    # Monthly burn: 3000 + (1200 / 12) = 3100
    assert kpis["total_monthly_burn"] == 3100.0
    assert "Ravi" in kpis["by_payer"]
    assert "Company" in kpis["by_payer"]
    assert kpis["by_payer"]["Ravi"]["total_amount"] == 3000.0
    assert kpis["by_payer"]["Company"]["total_amount"] == 1200.0

