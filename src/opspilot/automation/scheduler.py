import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from aiogram.types import InlineKeyboardMarkup

from opspilot.automation.auto_prune import execute_auto_prune
from opspilot.chatops.telegram.keyboards import get_container_alert_keyboard
from opspilot.config import Settings
from opspilot.core.executor import SafeOperationExecutor
from opspilot.core.ignored import IgnoredContainersManager
from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.ssl import check_domain_ssl
from opspilot.monitor.system import collect_system_metrics

logger = logging.getLogger("opspilot.scheduler")


class BackgroundScheduler:
    def __init__(
        self,
        settings: Settings,
        notify_callback: Callable[[str, InlineKeyboardMarkup | None], Coroutine[Any, Any, None]] | None = None,
        ignored_manager: IgnoredContainersManager | None = None,
    ):
        self.settings = settings
        self.notify_callback = notify_callback
        self.executor = SafeOperationExecutor()
        self.ignored_manager = ignored_manager or IgnoredContainersManager()
        self.running = False

    async def start(self):
        self.running = True
        logger.info(f"Background scheduler started (interval: {self.settings.monitoring.interval_seconds}s)")
        while self.running:
            try:
                await self._run_health_loop()
            except Exception as e:
                logger.error(f"Scheduler health loop error: {e}")
            await asyncio.sleep(self.settings.monitoring.interval_seconds)

    async def stop(self):
        self.running = False

    async def _run_health_loop(self):
        metrics = collect_system_metrics()

        # 1. Check Disk Threshold
        if metrics.disk_percent >= self.settings.monitoring.thresholds.disk_percent_critical:
            msg = (
                f"🚨 *CRITICAL DISK ALERT*\n"
                f"Server: `{self.settings.server_name}`\n"
                f"Disk Usage: *{metrics.disk_percent}%* ({metrics.disk_free_gb} GB free)"
            )
            if self.notify_callback:
                await self.notify_callback(msg, None)

            # Auto-prune if enabled
            if self.settings.automation.auto_prune_disk.enabled:
                res = await execute_auto_prune(
                    self.executor, metrics.disk_percent, self.settings.automation.auto_prune_disk.trigger_percent
                )
                if res.get("pruned") and self.notify_callback:
                    await self.notify_callback(
                        f"🧹 *Auto-Prune Action*: Reclaimed {res.get('reclaimed_mb')} MB of Docker cache.",
                        None,
                    )

        # 2. Check Docker Containers
        containers = collect_docker_statuses()
        for c in containers:
            if self.ignored_manager.is_ignored(c.name):
                continue
            if c.health == "unhealthy" or (c.status == "exited" and not c.name.startswith("run-")):
                msg = (
                    f"⚠️ *CONTAINER ISSUE*\n"
                    f"Server: `{self.settings.server_name}`\n"
                    f"Container: `{c.name}`\n"
                    f"Status: *{c.status}* | Health: *{c.health}*"
                )
                kb = get_container_alert_keyboard(c.name)
                if self.notify_callback:
                    await self.notify_callback(msg, kb)

        # 3. Check SSL Expirations
        for domain in self.settings.monitoring.ssl_domains:
            ssl_res = check_domain_ssl(domain)
            if ssl_res.is_valid and ssl_res.days_remaining <= 14:
                msg = f"🔒 *SSL EXPIRING SOON*\nDomain: `{domain}`\nExpires in: *{ssl_res.days_remaining} days* ({ssl_res.expires_at})"
                if self.notify_callback:
                    await self.notify_callback(msg, None)
