from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_confirmation_keyboard(action: str, target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{action}:{target}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:{action}:{target}"),
            ]
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
            ]
        ]
    )
