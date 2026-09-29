"""Telegram implementation of NotificationChannel."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from opspilot.channels.base import NotificationChannel
from opspilot.db.alert_routes import get_matching_chat_ids

logger = logging.getLogger("opspilot.channel.telegram")

PLACEHOLDER_CHAT_IDS = {"-1001234567890", "123456789", "YOUR_CHAT_ID", ""}


class TelegramChannel(NotificationChannel):
    def __init__(self, bot: Bot, chat_id: str) -> None:
        self._bot = bot
        self._chat_id = chat_id

    def update_chat_id(self, chat_id: str) -> None:
        self._chat_id = chat_id

    @property
    def chat_id(self) -> str:
        return self._chat_id

    async def send_to_chat(self, chat_id: str, text: str, keyboard: Any | None = None) -> bool:
        """Send message to a specific chat ID directly. Returns True if succeeded."""
        cid = chat_id.strip()
        if not cid or cid in PLACEHOLDER_CHAT_IDS:
            logger.warning(f"Telegram channel: no valid chat_id configured ({cid}), skipping alert.")
            return False
        try:
            markup: InlineKeyboardMarkup | None = keyboard
            await self._bot.send_message(
                chat_id=cid,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            err = str(e)
            if "chat not found" in err.lower():
                logger.warning(f"Telegram alert skipped: chat '{cid}' not found.")
            else:
                logger.error(f"Telegram send failed to {cid}: {e}")
            return False

    async def send(
        self,
        text: str,
        keyboard: Any | None = None,
        category: str = "general",
        target: str = "",
        event: str = "",
    ) -> None:
        """
        Route and send message to all matching chat IDs based on category and event/target.
        Falls back to default chat_id if no custom route matched.
        """
        try:
            chat_ids = await get_matching_chat_ids(
                category=category,
                target=target,
                event=event,
                default_chat_id=self._chat_id,
            )
        except Exception as e:
            logger.error(f"Error querying alert routes: {e}, falling back to default chat_id")
            chat_ids = [self._chat_id] if self._chat_id else []

        if not chat_ids:
            logger.warning("Telegram channel: no destination chat IDs matched, skipping alert.")
            return

        for cid in chat_ids:
            await self.send_to_chat(cid, text, keyboard)
