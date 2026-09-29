"""
Abstract notification channel interface.

Telegram is the first implementation. Future implementations (email, Slack, webhook)
only need to subclass NotificationChannel and implement `send`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    @abstractmethod
    async def send(self, text: str, keyboard: Any | None = None) -> None:
        """Send a notification with optional interactive keyboard."""
        ...

    async def send_plain(self, text: str) -> None:
        """Convenience method for keyboard-less messages."""
        await self.send(text, None)
