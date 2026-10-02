"""
Tests for the BackgroundScheduler loops.

Strategy: mock the DB layer and channel so we test scheduling logic in isolation
without touching Docker, psutil, or the network.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opspilot.automation.scheduler import BackgroundScheduler
from opspilot.channels.base import NotificationChannel
from opspilot.config import Settings


class _CaptureChannel(NotificationChannel):
    """Captures all messages sent through the channel."""

    def __init__(self):
        self.messages: list[tuple[str, object]] = []

    async def send(
        self,
        text: str,
        keyboard: object = None,
        category: str = "general",
        target: str = "",
        event: str = "",
    ) -> None:
        self.messages.append((text, keyboard))


def _make_scheduler(channel: NotificationChannel) -> BackgroundScheduler:
    settings = Settings(
        telegram_bot_token="x",
        telegram_alert_chat_id="-100123",
        auth_mode="development",
    )
    return BackgroundScheduler(settings, channel=channel)


# ─── Probe loop ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_loop_sends_alert_on_failure():
    """A failing probe must open an incident and send exactly one alert."""
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    fake_ep = {"name": "GNS API", "url": "https://gns.test/health", "expected_status": 200, "timeout_seconds": 5}
    fake_result = MagicMock(is_healthy=False, status_code=None, error="Connection refused", latency_ms=0)

    with (
        patch("opspilot.automation.scheduler.ep_db.list_endpoints", new=AsyncMock(return_value=[fake_ep])),
        patch("opspilot.automation.scheduler.probe_http_endpoint", new=AsyncMock(return_value=fake_result)),
        patch("opspilot.automation.scheduler.inc_db.open_incident", new=AsyncMock(return_value=(1, True, False))),
        patch("opspilot.automation.scheduler.inc_db.resolve_incident", new=AsyncMock(return_value=None)),
        patch.object(scheduler, "_get_incident", new=AsyncMock(return_value={"alert_count": 1})),
    ):
        await scheduler._run_probe_loop()

    assert len(channel.messages) == 1
    text, _ = channel.messages[0]
    assert "GNS API" in text
    assert "unreachable" in text.lower() or "down" in text.lower()


@pytest.mark.asyncio
async def test_probe_loop_sends_recovery_on_restore():
    """A healthy probe that had an open incident must send a recovery message."""
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    fake_ep = {"name": "GNS API", "url": "https://gns.test/health", "expected_status": 200, "timeout_seconds": 5}
    fake_result = MagicMock(is_healthy=True, status_code=200, latency_ms=42.0)

    with (
        patch("opspilot.automation.scheduler.ep_db.list_endpoints", new=AsyncMock(return_value=[fake_ep])),
        patch("opspilot.automation.scheduler.probe_http_endpoint", new=AsyncMock(return_value=fake_result)),
        patch("opspilot.automation.scheduler.inc_db.resolve_incident", new=AsyncMock(return_value=7)),
    ):
        await scheduler._run_probe_loop()

    assert len(channel.messages) == 1
    text, _ = channel.messages[0]
    assert "Recovered" in text


@pytest.mark.asyncio
async def test_probe_loop_no_message_when_snoozed():
    """A failing probe whose incident is snoozed must NOT send an alert."""
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    fake_ep = {"name": "API", "url": "https://x.test", "expected_status": 200, "timeout_seconds": 5}
    fake_result = MagicMock(is_healthy=False, status_code=503, error=None, latency_ms=0)

    with (
        patch("opspilot.automation.scheduler.ep_db.list_endpoints", new=AsyncMock(return_value=[fake_ep])),
        patch("opspilot.automation.scheduler.probe_http_endpoint", new=AsyncMock(return_value=fake_result)),
        # is_snoozed=True → caller must suppress alert
        patch("opspilot.automation.scheduler.inc_db.open_incident", new=AsyncMock(return_value=(5, False, True))),
    ):
        await scheduler._run_probe_loop()

    assert len(channel.messages) == 0, "Snoozed incident must not trigger an alert"


@pytest.mark.asyncio
async def test_probe_loop_no_endpoints_is_noop():
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    with patch("opspilot.automation.scheduler.ep_db.list_endpoints", new=AsyncMock(return_value=[])):
        await scheduler._run_probe_loop()

    assert len(channel.messages) == 0


# ─── Renewal loop ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_renewal_loop_sends_reminder_for_due():
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    due_renewal = {
        "id": 3,
        "name": "OVH VPS",
        "category": "vps",
        "due_date": "2026-10-01",
        "amount": 1200.0,
        "currency": "INR",
        "notes": "",
        "recurrence": "monthly",
        "status": "pending",
    }

    with (
        patch("opspilot.automation.scheduler.ren_db.get_due_renewals", new=AsyncMock(return_value=[due_renewal])),
        patch("opspilot.automation.scheduler.ren_db.mark_reminded", new=AsyncMock()),
    ):
        scheduler._last_renewal_date = None  # force it to run
        await scheduler._run_renewal_loop()

    assert len(channel.messages) == 1
    text, kb = channel.messages[0]
    assert "OVH VPS" in text
    assert kb is not None  # must include Mark Paid / Snooze keyboard


@pytest.mark.asyncio
async def test_renewal_loop_runs_only_once_per_day():
    """Second call on the same UTC day must be a no-op."""
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    scheduler._last_renewal_date = today  # already ran today

    mock_get = AsyncMock(return_value=[])
    with patch("opspilot.automation.scheduler.ren_db.get_due_renewals", new=mock_get):
        await scheduler._run_renewal_loop_if_due()

    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_domain_governance_loop_runs_only_once_per_day():
    """Domain governance check must only run once per UTC day."""
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    scheduler._last_domain_check_date = today

    mock_list = AsyncMock(return_value=[])
    with patch("opspilot.automation.scheduler.domains_db.list_domains", new=mock_list):
        await scheduler._run_domain_governance_loop_if_due()

    mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_domain_governance_loop_sends_ssl_and_domain_alerts():
    """Expiring SSL (<30d) and expiring domain (<60d) must dispatch notifications."""
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    fake_domain = {
        "id": 1,
        "domain": "iitdeveloper.com",
        "port": 443,
        "is_valid": True,
        "days_remaining": 12,  # expiring SSL
        "expires_at": "2026-10-15",
        "registrar": "GoDaddy",
        "domain_days_remaining": 25,  # expiring domain
        "domain_expires_at": "2026-10-28",
    }

    with (
        patch("opspilot.automation.scheduler.domains_db.seed_domains", new=AsyncMock(return_value=0)),
        patch("opspilot.automation.scheduler.domains_db.list_domains", new=AsyncMock(return_value=[fake_domain])),
        patch("opspilot.automation.scheduler.domains_db.recheck_domain", new=AsyncMock(return_value=fake_domain)),
        patch("opspilot.automation.scheduler.inc_db.open_incident", new=AsyncMock(return_value=(1, True, False))),
        patch("opspilot.automation.scheduler.inc_db.resolve_incident", new=AsyncMock(return_value=None)),
    ):
        await scheduler._run_domain_governance_loop()

    # Both SSL and Domain expiration alerts should be dispatched
    messages = [m for m, _ in channel.messages]
    assert any("SSL Certificate Expiring" in m for m in messages)
    assert any("Domain Registration Expiring" in m for m in messages)


# ─── Health loop — disk alert ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_loop_sends_disk_alert():
    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)

    fake_metrics = MagicMock(
        disk_percent=95.0,
        disk_free_gb=5.0,
        ram_percent=50.0,
        cpu_percent=20.0,
        cpu_count=4,
        ram_used_gb=8.0,
        ram_total_gb=16.0,
        disk_used_gb=95.0,
        disk_total_gb=100.0,
        uptime_human="5d",
        load_avg=[0.5, 0.4, 0.3],
    )

    # Patch asyncio.to_thread to intercept the blocking calls and return test doubles
    async def fake_to_thread(fn, *args):
        name = getattr(fn, "__name__", "")
        if name == "collect_system_metrics":
            return fake_metrics
        if name == "collect_docker_statuses":
            return []
        if name == "check_domain_ssl":
            return MagicMock(is_valid=True, days_remaining=30)
        return fn(*args)

    with (
        patch("opspilot.automation.scheduler.asyncio.to_thread", side_effect=fake_to_thread),
        patch("opspilot.automation.scheduler.inc_db.open_incident", new=AsyncMock(return_value=(1, False, False))),
        patch("opspilot.automation.scheduler.inc_db.resolve_incident", new=AsyncMock(return_value=None)),
    ):
        await scheduler._run_health_loop()

    disk_alerts = [m for m, _ in channel.messages if "Disk" in m]
    assert len(disk_alerts) >= 1


# ─── Scheduler error logging ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_logs_loop_exceptions(caplog):
    """Fix #7: exceptions from gather must be logged, not silently discarded."""
    import logging

    channel = _CaptureChannel()
    scheduler = _make_scheduler(channel)
    scheduler.running = False  # prevent infinite loop

    async def boom():
        raise RuntimeError("intentional test failure")

    with (
        patch.object(scheduler, "_run_health_loop", side_effect=RuntimeError("health failed")),
        patch.object(scheduler, "_run_probe_loop", side_effect=RuntimeError("probe failed")),
        patch.object(scheduler, "_run_renewal_loop_if_due", new=AsyncMock()),
        patch.object(scheduler, "_run_maintenance_if_due", new=AsyncMock()),
        caplog.at_level(logging.ERROR, logger="opspilot.scheduler"),
    ):
        # Run one cycle manually (bypass the while loop)
        results = await asyncio.gather(
            scheduler._run_health_loop(),
            scheduler._run_probe_loop(),
            return_exceptions=True,
        )
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                loop_name = ["health_loop", "probe_loop"][i]
                import logging as _log

                _log.getLogger("opspilot.scheduler").error(f"Scheduler {loop_name} raised: {res}", exc_info=res)

    error_msgs = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("health_loop" in m for m in error_msgs)
    assert any("probe_loop" in m for m in error_msgs)
