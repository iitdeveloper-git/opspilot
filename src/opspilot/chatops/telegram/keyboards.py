from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_confirmation_keyboard(action: str, target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{action}:{target}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:{action}:{target}"),
            ]
        ]
    )


def get_container_alert_keyboard(container_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Logs", callback_data=f"act:logs:{container_name}"),
                InlineKeyboardButton(text="🔄 Restart", callback_data=f"act:restart:{container_name}"),
                InlineKeyboardButton(text="🔇 Ignore", callback_data=f"act:ignore:{container_name}"),
            ]
        ]
    )


def get_ignore_duration_keyboard(container_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏳ 1 Hour", callback_data=f"snooze:{container_name}:1h"),
                InlineKeyboardButton(text="⏳ 24 Hours", callback_data=f"snooze:{container_name}:24h"),
            ],
            [
                InlineKeyboardButton(text="⏳ 7 Days", callback_data=f"snooze:{container_name}:7d"),
                InlineKeyboardButton(text="♾️ Indefinitely", callback_data=f"snooze:{container_name}:forever"),
            ],
            [
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:ignore:{container_name}"),
            ],
        ]
    )


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Status", callback_data="cmd:status"),
                InlineKeyboardButton(text="🐳 Containers", callback_data="cmd:ps"),
            ],
            [
                InlineKeyboardButton(text="💾 Disk Details", callback_data="cmd:disk"),
                InlineKeyboardButton(text="🧹 Clean Cache", callback_data="cmd:clean"),
            ],
            [
                InlineKeyboardButton(text="🔒 SSL Status", callback_data="cmd:ssl"),
                InlineKeyboardButton(text="🤖 Ask Copilot", callback_data="cmd:ask"),
            ],
        ]
    )
