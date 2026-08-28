import asyncio
import logging

from aiogram.types import InlineKeyboardMarkup

from opspilot.automation.scheduler import BackgroundScheduler
from opspilot.chatops.telegram.bot import create_bot_app
from opspilot.config import load_settings
from opspilot.core.ignored import IgnoredContainersManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("opspilot.main")

# Dummy placeholder IDs from example configs to ignore
PLACEHOLDER_CHAT_IDS = {"-1001234567890", "123456789", "YOUR_CHAT_ID", ""}


async def run_daemon(config_path: str | None = None):
    settings = load_settings(config_path)
    logger.info(f"Starting OpsPilot Daemon for server: {settings.server_name}")

    ignored_manager = IgnoredContainersManager()
    bot, dp = None, None

    async def telegram_notifier(text: str, reply_markup: InlineKeyboardMarkup | None = None):
        chat_id = settings.telegram_alert_chat_id.strip()
        if not bot or not chat_id or chat_id in PLACEHOLDER_CHAT_IDS:
            return

        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            err_str = str(e)
            if "chat not found" in err_str.lower():
                logger.warning(
                    f"Telegram alert not delivered: Chat ID '{chat_id}' was not found. "
                    "Set TELEGRAM_ALERT_CHAT_ID to your real Telegram user ID in .env, "
                    "or add your bot as an admin to the alert group."
                )
            else:
                logger.error(f"Failed to send Telegram alert to {chat_id}: {e}")

    scheduler = BackgroundScheduler(settings, notify_callback=telegram_notifier, ignored_manager=ignored_manager)
    scheduler_task = asyncio.create_task(scheduler.start())

    if settings.telegram_bot_token:
        bot, dp = create_bot_app(settings, ignored_manager=ignored_manager)
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
