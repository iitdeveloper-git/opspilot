import asyncio
import logging
from opspilot.config import load_settings
from opspilot.chatops.telegram.bot import create_bot_app
from opspilot.automation.scheduler import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("opspilot.main")


async def run_daemon(config_path: str | None = None):
    settings = load_settings(config_path)
    logger.info(f"Starting OpsPilot Daemon for server: {settings.server_name}")

    bot, dp = None, None
    async def telegram_notifier(text: str):
        if bot and settings.telegram_alert_chat_id:
            try:
                await bot.send_message(chat_id=settings.telegram_alert_chat_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")

    scheduler = BackgroundScheduler(settings, notify_callback=telegram_notifier)
    scheduler_task = asyncio.create_task(scheduler.start())

    if settings.telegram_bot_token:
        bot, dp = create_bot_app(settings)
        logger.info("Telegram Bot initialized. Polling for commands...")
        try:
            await dp.start_polling(bot)
        finally:
            await scheduler.stop()
            scheduler_task.cancel()
    else:
        logger.warning("No TELEGRAM_BOT_TOKEN set. Running in headless monitoring mode.")
        await scheduler_task


def main():
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
