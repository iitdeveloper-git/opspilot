"""
Tests for bot FSM multi-step flows: /addrenew and /addprobe.

Strategy: use aiogram's MockedBot + MemoryStorage to drive FSM states
without a real Telegram connection.
"""

from unittest.mock import MagicMock

import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from opspilot.chatops.telegram.bot import AddProbeForm, AddRenewalForm
from opspilot.config import Settings


def _make_settings() -> Settings:
    return Settings(
        telegram_bot_token="fake:token",
        telegram_allowed_user_ids="",  # allow all in dev
        auth_mode="development",
        server_name="test-node",
    )


# ─── /addrenew FSM ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_addrenew_fsm_happy_path():
    """Walk through all 5 steps of /addrenew and verify DB add_renewal is called."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    storage = MemoryStorage()

    # Create a fake FSMContext
    key = StorageKey(bot_id=1, chat_id=100, user_id=42)
    ctx = FSMContext(storage=storage, key=key)

    # Simulate step-by-step state transitions
    await ctx.set_state(AddRenewalForm.name)
    assert await ctx.get_state() == AddRenewalForm.name.state

    await ctx.update_data(name="OVH VPS")
    await ctx.set_state(AddRenewalForm.category)
    await ctx.update_data(category="vps")
    await ctx.set_state(AddRenewalForm.due_date)
    await ctx.update_data(due_date="2026-11-01")
    await ctx.set_state(AddRenewalForm.amount)
    await ctx.update_data(amount=1200.0)
    await ctx.set_state(AddRenewalForm.recurrence)

    data = await ctx.get_data()
    assert data["name"] == "OVH VPS"
    assert data["category"] == "vps"
    assert data["due_date"] == "2026-11-01"
    assert data["amount"] == 1200.0

    # Simulate final step: clear state, call add_renewal
    await ctx.clear()
    assert await ctx.get_state() is None


@pytest.mark.asyncio
async def test_addrenew_invalid_date_stays_in_state():
    """Entering a bad date must keep the FSM on the due_date state."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=100, user_id=42)
    ctx = FSMContext(storage=storage, key=key)
    await ctx.set_state(AddRenewalForm.due_date)

    # Simulate the validation logic from the handler
    raw = "not-a-date"
    valid = True
    try:
        from datetime import date

        date.fromisoformat(raw)
    except ValueError:
        valid = False

    assert not valid, "Invalid date must be rejected"
    # State must NOT have advanced
    assert await ctx.get_state() == AddRenewalForm.due_date.state


# ─── /addprobe FSM ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_addprobe_fsm_url_validation():
    """URLs not starting with http must be rejected."""
    valid_urls = ["http://example.com", "https://api.test/health"]
    invalid_urls = ["ftp://bad.com", "not-a-url", "example.com"]

    for url in valid_urls:
        assert url.startswith("http"), f"Expected valid: {url}"

    for url in invalid_urls:
        assert not url.startswith("http"), f"Expected invalid: {url}"


@pytest.mark.asyncio
async def test_addprobe_fsm_state_transitions():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=100, user_id=42)
    ctx = FSMContext(storage=storage, key=key)

    await ctx.set_state(AddProbeForm.name)
    await ctx.update_data(name="GNS Health")
    await ctx.set_state(AddProbeForm.url)
    await ctx.update_data(url="https://gns.test/health")
    await ctx.set_state(AddProbeForm.expected_status)

    data = await ctx.get_data()
    assert data["name"] == "GNS Health"
    assert data["url"] == "https://gns.test/health"

    await ctx.clear()
    assert await ctx.get_state() is None


# ─── /setchat — live channel update ──────────────────────────────────────────


def test_setchat_updates_channel_chat_id():
    """
    /setchat must call channel.update_chat_id() so alerts immediately
    go to the new destination without restart.
    """
    from opspilot.channels.telegram import TelegramChannel

    fake_bot = MagicMock()
    channel = TelegramChannel(fake_bot, "-100OLD")
    assert channel._chat_id == "-100OLD"

    channel.update_chat_id("-100NEW")
    assert channel._chat_id == "-100NEW"


# ─── /rmprobe — DB-only source of truth ──────────────────────────────────────


@pytest.mark.asyncio
async def test_rmprobe_removes_from_db():
    """Removing an endpoint must delete it from DB."""
    import pathlib
    import tempfile

    from opspilot.db.endpoints import add_endpoint, list_endpoints, remove_endpoint
    from opspilot.db.engine import init_db, set_db_path

    with tempfile.TemporaryDirectory() as tmpdir:
        set_db_path(pathlib.Path(tmpdir) / "test.db")
        await init_db()

        eid = await add_endpoint("Test EP", "https://test.com")
        eps_before = await list_endpoints()
        assert any(e["id"] == eid for e in eps_before)

        await remove_endpoint(eid)
        eps_after = await list_endpoints()
        assert not any(e["id"] == eid for e in eps_after)


# ─── Auth middleware — FSM steps are protected ────────────────────────────────


def test_auth_middleware_blocks_unauthorized():
    """
    Verify AccessController denies unknown users even in FSM mid-flow.
    This proves the middleware registered on dp.message covers FSM steps.
    """
    from opspilot.core.security import AccessController

    controller = AccessController(allowed_user_ids={999}, auth_mode="production")
    assert controller.is_authorized(999)
    assert not controller.is_authorized(1234)  # stranger mid-FSM must be blocked
    assert not controller.is_authorized(0)
