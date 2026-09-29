"""
OpsPilot 2.0 Background Scheduler.

Fix #6: Endpoints are DB-only after initial seed. No YAML fallback in the scheduler.
Fix #7: asyncio.gather exceptions are logged, not silently discarded.

Loops:
  1. _run_health_loop   — Docker containers + disk (every interval_seconds)
  2. _run_probe_loop    — HTTP endpoint uptime from DB (every interval_seconds)
  3. _run_renewal_loop  — Billing reminders, once per UTC day
  4. _run_maintenance   — Incident pruning + snooze cleanup, hourly
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from opspilot.automation.auto_prune import execute_auto_prune
from opspilot.channels.base import NotificationChannel
from opspilot.chatops.telegram.keyboards import (
    get_container_alert_keyboard,
    get_probe_alert_keyboard,
    get_renewal_alert_keyboard,
)
from opspilot.chatops.telegram.templates import (
    container_alert,
    container_recovered,
    disk_critical,
    probe_down,
    probe_recovered,
    renewal_reminder,
    ssl_expiring,
)
from opspilot.config import Settings
from opspilot.core.executor import SafeOperationExecutor
from opspilot.db import endpoints as ep_db
from opspilot.db import incidents as inc_db
from opspilot.db import renewals as ren_db
from opspilot.db import snooze as snooze_db
from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.probes import probe_http_endpoint
from opspilot.monitor.ssl import check_domain_ssl
from opspilot.monitor.system import collect_system_metrics

logger = logging.getLogger("opspilot.scheduler")


class BackgroundScheduler:
    def __init__(
        self,
        settings: Settings,
        channel: NotificationChannel | None = None,
        notify_callback=None,  # legacy compat
    ):
        self.settings = settings
        self.channel = channel
        self._legacy_cb = notify_callback
        self.executor = SafeOperationExecutor()
        self.running = False
        self._last_renewal_date: str | None = None
        self._last_maintenance: datetime | None = None

    async def start(self) -> None:
        self.running = True
        logger.info(f"Scheduler started (interval={self.settings.monitoring.interval_seconds}s)")
        while self.running:
            # Fix #7: run loops, log any exceptions — don't discard them silently
            results = await asyncio.gather(
                self._run_health_loop(),
                self._run_probe_loop(),
                return_exceptions=True,
            )
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    loop_name = ["health_loop", "probe_loop"][i]
                    logger.error(f"Scheduler {loop_name} raised: {res}", exc_info=res)

            try:
                await self._run_renewal_loop_if_due()
            except Exception as e:
                logger.error(f"Scheduler renewal_loop raised: {e}", exc_info=e)

            try:
                await self._run_maintenance_if_due()
            except Exception as e:
                logger.error(f"Scheduler maintenance raised: {e}", exc_info=e)

            await asyncio.sleep(self.settings.monitoring.interval_seconds)

    async def stop(self) -> None:
        self.running = False

    async def _notify(
        self,
        text: str,
        keyboard=None,
        category: str = "general",
        target: str = "",
        event: str = "",
    ) -> None:
        if self.channel:
            await self.channel.send(
                text, keyboard, category=category, target=target, event=event
            )
        elif self._legacy_cb:
            await self._legacy_cb(text, keyboard)

    # ─── Loop 1: Container / Disk Health ─────────────────────────────────────

    async def _run_health_loop(self) -> None:
        metrics = await asyncio.to_thread(collect_system_metrics)

        if metrics.disk_percent >= self.settings.monitoring.thresholds.disk_percent_critical:
            await self._notify(disk_critical(self.settings.server_name, metrics.disk_percent, metrics.disk_free_gb), category="system", target="disk", event="disk_critical")
            if self.settings.automation.auto_prune_disk.enabled:
                res = await execute_auto_prune(
                    self.executor,
                    metrics.disk_percent,
                    self.settings.automation.auto_prune_disk.trigger_percent,
                )
                if res.get("pruned"):
                    await self._notify(
                        f"🧹 <b>Auto-Prune Complete</b>\nReclaimed <b>{res.get('reclaimed_mb')} MB</b> of Docker cache."
                    )

        containers = await asyncio.to_thread(collect_docker_statuses)
        for c in containers:
            is_bad = c.health == "unhealthy" or (c.status == "exited" and not c.name.startswith("run-"))
            if is_bad:
                snoozed = await snooze_db.is_snoozed(c.name)
                if not snoozed:
                    inc_id, is_new, inc_snoozed = await inc_db.open_incident(
                        source="docker",
                        target=c.name,
                        severity="critical",
                        title=f"Container {c.name} is {c.status}",
                        detail=f"status={c.status} health={c.health}",
                    )
                    if not inc_snoozed:
                        inc = await self._get_incident(inc_id)
                        count = inc["alert_count"] if inc else 1
                        if is_new or count % 5 == 0:
                            msg = container_alert(c.name, c.status, c.health, self.settings.server_name, count)
                            await self._notify(msg, get_container_alert_keyboard(c.name), category="containers", target=c.name, event="container_down")
            else:
                resolved_id = await inc_db.resolve_incident("docker", c.name)
                if resolved_id:
                    await self._notify(container_recovered(c.name, self.settings.server_name), category="containers", target=c.name, event="container_recovered")

        # SSL — route through incidents for dedupe (Fix #8 partial)
        for domain in self.settings.monitoring.ssl_domains:
            ssl_res = await asyncio.to_thread(check_domain_ssl, domain)
            if ssl_res.is_valid and ssl_res.days_remaining <= 14:
                inc_id, is_new, inc_snoozed = await inc_db.open_incident(
                    source="ssl",
                    target=domain,
                    severity="warning",
                    title=f"SSL expiring: {domain}",
                    detail=f"{ssl_res.days_remaining} days left",
                )
                if is_new and not inc_snoozed:
                    await self._notify(ssl_expiring(domain, ssl_res.days_remaining, ssl_res.expires_at), category="ssl", target=domain, event="ssl_expiring")
            else:
                await inc_db.resolve_incident("ssl", domain)

    async def _get_incident(self, inc_id: int) -> dict | None:
        from opspilot.db.engine import db_conn

        async with db_conn() as db:
            cursor = await db.execute("SELECT * FROM incidents WHERE id=?", (inc_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ─── Loop 2: HTTP Probe ────────────────────────────────────────────────────

    async def _run_probe_loop(self) -> None:
        # Fix #6: DB-only. No YAML fallback merge in the scheduler.
        endpoints = await ep_db.list_endpoints(enabled_only=True)
        if not endpoints:
            return

        tasks = [
            probe_http_endpoint(
                ep["name"],
                ep["url"],
                ep.get("expected_status", 200),
                ep.get("timeout_seconds", 5),
            )
            for ep in endpoints
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for ep, result in zip(endpoints, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(f"Probe task exception for {ep['name']}: {result}")
                continue

            if not result.is_healthy:
                inc_id, is_new, inc_snoozed = await inc_db.open_incident(
                    source="http_probe",
                    target=ep["url"],
                    severity="critical",
                    title=f"{ep['name']} is unreachable",
                    detail=result.error or f"HTTP {result.status_code}",
                )
                if not inc_snoozed:
                    inc = await self._get_incident(inc_id)
                    count = inc["alert_count"] if inc else 1
                    if is_new or count % 5 == 0:
                        msg = probe_down(ep["name"], ep["url"], result.status_code, result.error, count)
                        await self._notify(msg, get_probe_alert_keyboard(inc_id), category="probes", target=ep["name"], event="probe_failed")
            else:
                resolved_id = await inc_db.resolve_incident("http_probe", ep["url"])
                if resolved_id:
                    await self._notify(probe_recovered(ep["name"], ep["url"], result.latency_ms), category="probes", target=ep["name"], event="probe_recovered")

    # ─── Loop 3: Renewal Reminders (once per UTC day) ─────────────────────────

    async def _run_renewal_loop_if_due(self) -> None:
        today_utc = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._last_renewal_date == today_utc:
            return
        self._last_renewal_date = today_utc
        await self._run_renewal_loop()

    async def _run_renewal_loop(self) -> None:
        due = await ren_db.get_due_renewals()
        for renewal in due:
            msg = renewal_reminder(renewal)
            kb = get_renewal_alert_keyboard(renewal["id"])
            await self._notify(msg, kb, category="billing", target=renewal.get("name", ""), event="renewal_due")
            await ren_db.mark_reminded(renewal["id"])

    # ─── Loop 4: Maintenance (hourly) ─────────────────────────────────────────

    async def _run_maintenance_if_due(self) -> None:
        now = datetime.now(UTC)
        if self._last_maintenance and (now - self._last_maintenance) < timedelta(hours=1):
            return
        self._last_maintenance = now
        pruned = await inc_db.prune_old_incidents(days=30)
        cleaned = await snooze_db.cleanup_expired_snoozes()
        if pruned or cleaned:
            logger.info(f"Maintenance: pruned {pruned} old incidents, cleared {cleaned} expired snoozes.")
