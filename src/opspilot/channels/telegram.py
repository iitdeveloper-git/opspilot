"""Telegram implementation of NotificationChannel."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from opspilot.channels.base import NotificationChannel

logger = logging.getLogger("opspilot.channel.telegram")

PLACEHOLDER_CHAT_IDS = {"-1001234567890", "123456789", "YOUR_CHAT_ID", ""}


class TelegramChannel(NotificationChannel):
    def __init__(self, bot: Bot, chat_id: str) -> None:
        self._bot = bot
        self._chat_id = chat_id

    def update_chat_id(self, chat_id: str) -> None:
        self._chat_id = chat_id

    async def send(self, text: str, keyboard: Any | None = None) -> None:
        chat_id = self._chat_id.strip()
        if not chat_id or chat_id in PLACEHOLDER_CHAT_IDS:
            logger.warning("Telegram channel: no valid chat_id configured, skipping alert.")
            return
        try:
            markup: InlineKeyboardMarkup | None = keyboard
            await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except Exception as e:
            err = str(e)
            if "chat not found" in err.lower():
                logger.warning(f"Telegram alert skipped: chat '{chat_id}' not found.")
            else:
                logger.error(f"Telegram send failed to {chat_id}: {e}")
