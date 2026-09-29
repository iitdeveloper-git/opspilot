"""OpsPilot 2.0 — main entry point."""

import asyncio
import logging

from opspilot.automation.scheduler import BackgroundScheduler
from opspilot.channels.telegram import TelegramChannel
from opspilot.chatops.telegram.bot import create_bot_app
from opspilot.config import load_settings
from opspilot.core.ignored import IgnoredContainersManager
from opspilot.db.endpoints import seed_endpoints_from_yaml
from opspilot.db.engine import init_db, set_db_path
from opspilot.db.renewals import seed_renewals_from_yaml
from opspilot.db.store import get_setting

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("opspilot.main")


async def _migrate_ignored_json(ignored_manager: IgnoredContainersManager) -> None:
    """
    Fix #3 — one-time migration: import ignored_containers.json into container_snooze DB table.
    Renames the file to .migrated so it never runs again.
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from opspilot.db.snooze import snooze_container

    json_path = Path("audit_logs/ignored_containers.json")
    if not json_path.exists():
        return

    import json

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read {json_path}: {e}")
        return

    if isinstance(data, list):
        # Old format: plain list of container names → treat as indefinite snooze
        entries = {name: {"expires_at": None} for name in data}
    elif isinstance(data, dict):
        entries = data
    else:
        entries = {}

    migrated = 0
    for name, info in entries.items():
        expires_at_str = info.get("expires_at")
        exp_dt = None
        if expires_at_str:
            try:
                from datetime import datetime

                # Try parsing ISO format (may have +00:00 suffix from old code)
                exp_dt = datetime.fromisoformat(expires_at_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=UTC)
                # Skip already-expired entries
                if exp_dt <= datetime.now(UTC):
                    continue
            except ValueError:
                exp_dt = None
        await snooze_container(name, exp_dt)
        migrated += 1

    # Rename so migration never runs twice
    json_path.rename(json_path.with_suffix(".json.migrated"))
    if migrated:
        logger.info(f"Migrated {migrated} ignored containers from {json_path.name} → container_snooze DB.")


async def run_daemon(config_path: str | None = None) -> None:
    settings = load_settings(config_path)
    logger.info(f"Starting OpsPilot 2.0 for server: {settings.server_name}")

    # — DB init (creates tables, runs ALTER TABLE migrations)
    set_db_path(settings.db_path)
    await init_db()

    # — Fix #1: read persisted alert_chat_id from DB; fall back to YAML/env value
    alert_chat_id = await get_setting("alert_chat_id", settings.telegram_alert_chat_id)
    if alert_chat_id != settings.telegram_alert_chat_id:
        logger.info(f"Using persisted alert_chat_id from DB: {alert_chat_id}")

    # — Fix #2 + seed: endpoints and renewals seeded from YAML (idempotent)
    yaml_renewals = [r.model_dump() for r in settings.monitoring.initial_renewals]
    yaml_endpoints = [e.model_dump() for e in settings.monitoring.http_endpoints]
    seeded_r = await seed_renewals_from_yaml(yaml_renewals)
    seeded_e = await seed_endpoints_from_yaml(yaml_endpoints)
    if seeded_r:
        logger.info(f"Seeded {seeded_r} new renewals from config.yaml")
    if seeded_e:
        logger.info(f"Seeded {seeded_e} new endpoints from config.yaml")

    # — Fix #3: one-time migration of old JSON ignore list
    ignored_manager = IgnoredContainersManager()
    await _migrate_ignored_json(ignored_manager)

    if not settings.telegram_bot_token:
        logger.warning("No TELEGRAM_BOT_TOKEN set. Running in headless monitoring mode.")
        scheduler = BackgroundScheduler(settings)
        await scheduler.start()
        return

    from aiogram import Bot

    bot_client = Bot(token=settings.telegram_bot_token)
    # Fix #1: construct channel with the DB-persisted (or config) chat_id
    channel = TelegramChannel(bot_client, alert_chat_id)
    scheduler = BackgroundScheduler(settings, channel=channel)
    scheduler_task = asyncio.create_task(scheduler.start())

    bot, dp = create_bot_app(settings, ignored_manager=ignored_manager, channel=channel)
    logger.info("Telegram Bot ready. Polling for commands...")
    try:
        await dp.start_polling(bot)
    finally:
        await scheduler.stop()
        scheduler_task.cancel()
        await bot_client.session.close()


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
