"""All InlineKeyboardMarkup factories for OpsPilot Telegram ChatOps."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ─── Utility ──────────────────────────────────────────────────────────────────

def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


# ─── Generic confirmation ──────────────────────────────────────────────────────

def get_confirmation_keyboard(action: str, target: str) -> InlineKeyboardMarkup:
    return _kb([
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{action}:{target}"),
        InlineKeyboardButton(text="❌ Cancel",  callback_data=f"cancel:{action}:{target}"),
    ])


# ─── Main menu ────────────────────────────────────────────────────────────────

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return _kb(
        [
            InlineKeyboardButton(text="📊 Status",     callback_data="cmd:status"),
            InlineKeyboardButton(text="🐳 Containers", callback_data="cmd:ps"),
        ],
        [
            InlineKeyboardButton(text="💾 Disk",       callback_data="cmd:disk"),
            InlineKeyboardButton(text="🧹 Clean",      callback_data="cmd:clean"),
        ],
        [
            InlineKeyboardButton(text="🔒 SSL",        callback_data="cmd:ssl"),
            InlineKeyboardButton(text="🌐 Probes",     callback_data="cmd:probes"),
        ],
        [
            InlineKeyboardButton(text="💳 Renewals",   callback_data="cmd:renewals:1"),
            InlineKeyboardButton(text="📋 Incidents",  callback_data="cmd:incidents:1"),
        ],
        [
            InlineKeyboardButton(text="🤖 Ask Copilot", callback_data="cmd:ask"),
        ],
    )


# ─── Container alerts ─────────────────────────────────────────────────────────

def get_container_alert_keyboard(container_name: str) -> InlineKeyboardMarkup:
    return _kb(
        [
            InlineKeyboardButton(text="📋 Logs",     callback_data=f"act:logs:{container_name}"),
            InlineKeyboardButton(text="🔄 Restart",  callback_data=f"act:restart:{container_name}"),
        ],
        [
            InlineKeyboardButton(text="🔇 Snooze 1h",  callback_data=f"snooze:{container_name}:1h"),
            InlineKeyboardButton(text="🔇 Snooze 24h", callback_data=f"snooze:{container_name}:24h"),
            InlineKeyboardButton(text="🔕 Forever",    callback_data=f"snooze:{container_name}:forever"),
        ],
    )


def get_ignore_duration_keyboard(container_name: str) -> InlineKeyboardMarkup:
    return _kb(
        [
            InlineKeyboardButton(text="⏳ 1 Hour",   callback_data=f"snooze:{container_name}:1h"),
            InlineKeyboardButton(text="⏳ 24 Hours", callback_data=f"snooze:{container_name}:24h"),
        ],
        [
            InlineKeyboardButton(text="⏳ 7 Days",      callback_data=f"snooze:{container_name}:7d"),
            InlineKeyboardButton(text="♾️ Indefinitely", callback_data=f"snooze:{container_name}:forever"),
        ],
        [
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:ignore:{container_name}"),
        ],
    )


# ─── HTTP Probe alerts ────────────────────────────────────────────────────────

def get_probe_alert_keyboard(incident_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            InlineKeyboardButton(text="✅ Resolve",    callback_data=f"inc:resolve:{incident_id}"),
            InlineKeyboardButton(text="🔄 Recheck",   callback_data=f"inc:recheck:{incident_id}"),
        ],
        [
            InlineKeyboardButton(text="🔇 Snooze 1h",  callback_data=f"inc:snooze:{incident_id}:1h"),
            InlineKeyboardButton(text="🔇 Snooze 24h", callback_data=f"inc:snooze:{incident_id}:24h"),
        ],
    )


# ─── Renewal alerts ───────────────────────────────────────────────────────────

def get_renewal_alert_keyboard(renewal_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            InlineKeyboardButton(text="✅ Mark Paid",  callback_data=f"ren:paid:{renewal_id}"),
        ],
        [
            InlineKeyboardButton(text="🔇 Snooze 1d",  callback_data=f"ren:snooze:{renewal_id}:1d"),
            InlineKeyboardButton(text="🔇 Snooze 3d",  callback_data=f"ren:snooze:{renewal_id}:3d"),
            InlineKeyboardButton(text="🔇 Snooze 7d",  callback_data=f"ren:snooze:{renewal_id}:7d"),
        ],
    )


# ─── Pagination ───────────────────────────────────────────────────────────────

def get_pagination_keyboard(cmd: str, page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="◀ Prev", callback_data=f"cmd:{cmd}:{page - 1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Next ▶", callback_data=f"cmd:{cmd}:{page + 1}"))
    if not buttons:
        return None
    return _kb(buttons)


# ─── Probe list actions ───────────────────────────────────────────────────────

def get_probe_remove_keyboard(endpoint_id: int) -> InlineKeyboardMarkup:
    return _kb([
        InlineKeyboardButton(text="🗑 Remove", callback_data=f"ep:remove:{endpoint_id}"),
        InlineKeyboardButton(text="❌ Cancel", callback_data=f"ep:cancel:{endpoint_id}"),
    ])
