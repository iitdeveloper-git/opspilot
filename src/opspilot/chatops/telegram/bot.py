"""
OpsPilot 2.0 Telegram Bot — full ChatOps command centre.

Commands:
  /start /help /status /ps /logs /restart /ignore /unignore /ignored /ask
  /renewals   — paginated list with Mark Paid / Snooze buttons
  /incidents  — paginated open incident list
  /probes     — live HTTP endpoint status
  /addprobe   — add a new endpoint (multi-step)
  /rmprobe    — remove an endpoint by ID
  /addrenew   — add a renewal (multi-step conversation)
  /setchat    — update alert chat ID at runtime (hot config)
  /settings   — show current hot-configurable settings

All message formatting uses HTML via chatops.telegram.templates.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, TelegramObject

from opspilot.ai.copilot import OpsCopilot
from opspilot.ai.provider import AIProvider
from opspilot.chatops.telegram import keyboards as kb
from opspilot.chatops.telegram import templates as tpl
from opspilot.config import Settings
from opspilot.core.audit import AuditLogger
from opspilot.core.executor import SafeOperationExecutor
from opspilot.core.ignored import IgnoredContainersManager
from opspilot.core.security import AccessController
from opspilot.db import endpoints as ep_db
from opspilot.db import incidents as inc_db
from opspilot.db import renewals as ren_db
from opspilot.db import snooze as snooze_db
from opspilot.db.store import get_setting, set_setting
from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.probes import probe_http_endpoint
from opspilot.monitor.ssl import check_domain_ssl
from opspilot.monitor.system import collect_system_metrics

logger = logging.getLogger("opspilot.telegram")

PER_PAGE = 5

# ─── FSM States ───────────────────────────────────────────────────────────────


class AddRenewalForm(StatesGroup):
    name = State()
    category = State()
    due_date = State()
    amount = State()
    recurrence = State()


class AddProbeForm(StatesGroup):
    name = State()
    url = State()
    expected_status = State()


# ─── Auth Middleware ───────────────────────────────────────────────────────────


class AuthMiddleware(BaseMiddleware):
    def __init__(self, access: AccessController, audit: AuditLogger):
        super().__init__()
        self.access = access
        self.audit = audit

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = user.id if user else 0
        if not self.access.is_authorized(user_id):
            if isinstance(event, Message):
                await event.reply(
                    f"⛔ <b>Unauthorized</b>\nYour Telegram User ID: <code>{user_id}</code>",
                    parse_mode="HTML",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Unauthorized", show_alert=True)
            self.audit.record_action(user_id, "unauthorized_access", "bot", "BLOCKED")
            return None
        return await handler(event, data)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_duration_to_dt(duration_str: str) -> datetime | None:
    """Convert '1h', '24h', '7d', '1d', '3d' to UTC datetime. None = indefinite."""
    d = duration_str.lower().strip()
    if d in ("forever", "indefinite"):
        return None
    mapping = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
    }
    td = mapping.get(d)
    return datetime.now(UTC) + td if td else None


def _snooze_until_str(duration_str: str) -> str:
    dt = _parse_duration_to_dt(duration_str)
    if dt is None:
        return "indefinitely"
    return dt.strftime("%d %b %Y %H:%M UTC")


# ─── Bot Factory ──────────────────────────────────────────────────────────────


def create_bot_app(
    settings: Settings,
    ignored_manager: IgnoredContainersManager | None = None,
    channel=None,  # NotificationChannel — allows /setchat to hot-update alert destination
):
    bot = Bot(token=settings.telegram_bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    access = AccessController(settings.allowed_users, auth_mode=settings.auth_mode)
    audit = AuditLogger()
    executor = SafeOperationExecutor()
    # ignored_manager kept for backward compat; snooze now migrated to DB
    ai_provider = AIProvider(
        provider=settings.ai.provider,
        model=settings.ai.model,
        api_key=settings.ai.api_key,
        base_url=settings.ai.base_url,
    )
    copilot = OpsCopilot(ai_provider)

    auth_mw = AuthMiddleware(access, audit)
    dp.message.middleware(auth_mw)
    dp.callback_query.middleware(auth_mw)

    # ─── /start ───────────────────────────────────────────────────────────────

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        text = (
            f"👋 <b>Welcome to OpsPilot</b>\n"
            f"<i>Infrastructure Command Centre for <code>{settings.server_name}</code></i>\n\n"
            f"Use the menu below or type /help for all commands."
        )
        await message.reply(text, reply_markup=kb.get_main_menu_keyboard(), parse_mode="HTML")

    # ─── /help ────────────────────────────────────────────────────────────────

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        text = (
            f"🛠️ <b>OpsPilot Commands — {settings.server_name}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>🖥️ Infrastructure</b>\n"
            "  /status — CPU · RAM · Disk · Uptime\n"
            "  /ps — Docker container health\n"
            "  /logs <code>&lt;container&gt; [N]</code> — tail last N lines\n"
            "  /restart <code>&lt;container&gt;</code> — restart with confirmation\n\n"
            "<b>🔇 Snooze / Ignore</b>\n"
            "  /ignore <code>&lt;name&gt; [1h|24h|7d|forever]</code>\n"
            "  /unignore <code>&lt;name&gt;</code>\n"
            "  /ignored — list all snoozed containers\n\n"
            "<b>💳 Billing & Renewals</b>\n"
            "  /renewals — view pending renewals\n"
            "  /addrenew — add a renewal (guided)\n\n"
            "<b>🌐 HTTP Probes</b>\n"
            "  /probes — check all endpoints now\n"
            "  /addprobe — add new endpoint (guided)\n"
            "  /rmprobe <code>&lt;id&gt;</code> — remove endpoint\n\n"
            "<b>📋 Incidents</b>\n"
            "  /incidents — open incident list\n\n"
            "<b>⚙️ Config</b>\n"
            "  /setchat — set alert chat ID\n"
            "  /settings — show runtime settings\n\n"
            "<b>🤖 AI</b>\n"
            "  /ask <code>&lt;question&gt;</code> — AI diagnosis"
        )
        audit.record_action(message.from_user.id if message.from_user else 0, "help", "bot", "SUCCESS")
        await message.reply(text, parse_mode="HTML")

    # ─── /status ──────────────────────────────────────────────────────────────

    @dp.message(Command("status"))
    async def cmd_status(message: Message):
        m = collect_system_metrics()
        text = tpl.system_status(m, settings.server_name)
        audit.record_action(message.from_user.id if message.from_user else 0, "status", "system", "SUCCESS")
        await message.reply(text, reply_markup=kb.get_main_menu_keyboard(), parse_mode="HTML")

    # ─── /ps ──────────────────────────────────────────────────────────────────

    @dp.message(Command("ps"))
    async def cmd_ps(message: Message):
        containers = collect_docker_statuses()
        if not containers:
            await message.reply("🐳 <b>No active Docker containers found.</b>", parse_mode="HTML")
            return
        lines = [f"🐳 <b>Docker Containers ({len(containers)})</b>\n━━━━━━━━━━━━━━━━━━━━━"]
        for c in containers:
            snoozed = await snooze_db.is_snoozed(c.name)
            status_icon = "🟢" if c.status == "running" else "🔴"
            health_str = f" · <code>{c.health}</code>" if c.health != "none" else ""
            muted = " 🔇" if snoozed else ""
            lines.append(f"{status_icon} <code>{c.name}</code>{health_str}{muted}")
        audit.record_action(message.from_user.id if message.from_user else 0, "ps", "docker", "SUCCESS")
        await message.reply("\n".join(lines), parse_mode="HTML")

    # ─── /logs ────────────────────────────────────────────────────────────────

    @dp.message(Command("logs"))
    async def cmd_logs(message: Message, command: CommandObject):
        args = (command.args or "").split()
        if not args:
            await message.reply("Usage: /logs <code>&lt;container&gt; [lines]</code>", parse_mode="HTML")
            return
        container_name = args[0]
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 40
        result = await executor.run_command(["docker", "logs", "--tail", str(n), container_name])
        output = (result.get("stdout") or "") + (result.get("stderr") or "")
        text = (
            (f"📋 <b>Logs: {container_name}</b> (last {n} lines)\n━━━━━━━━━━━━━━━━━━━━━\n<pre>{output[:3500]}</pre>")
            if output
            else f"📋 <b>{container_name}</b>: No logs or container not found."
        )
        await message.reply(text, parse_mode="HTML")

    # ─── /restart ─────────────────────────────────────────────────────────────

    @dp.message(Command("restart"))
    async def cmd_restart(message: Message, command: CommandObject):
        name = (command.args or "").strip()
        if not name:
            await message.reply("Usage: /restart <code>&lt;container&gt;</code>", parse_mode="HTML")
            return
        await message.reply(
            f"⚠️ <b>Confirm Restart</b>\nRestart container <code>{name}</code> on <b>{settings.server_name}</b>?",
            reply_markup=kb.get_confirmation_keyboard("restart", name),
            parse_mode="HTML",
        )

    # ─── /ignore /unignore /ignored ───────────────────────────────────────────

    @dp.message(Command("ignore"))
    async def cmd_ignore(message: Message, command: CommandObject):
        args = (command.args or "").split()
        if not args:
            await message.reply("Usage: /ignore <code>&lt;container&gt; [1h|24h|7d|forever]</code>", parse_mode="HTML")
            return
        name = args[0]
        duration = args[1] if len(args) > 1 else "forever"
        exp_dt = _parse_duration_to_dt(duration)
        await snooze_db.snooze_container(name, exp_dt)
        until = exp_dt.strftime("%d %b %Y %H:%M UTC") if exp_dt else "indefinitely"
        await message.reply(
            f"🔇 <b>Alerts muted for <code>{name}</code></b>\n⏳ Until: {until}",
            parse_mode="HTML",
        )

    @dp.message(Command("unignore"))
    async def cmd_unignore(message: Message, command: CommandObject):
        name = (command.args or "").strip()
        if not name:
            await message.reply("Usage: /unignore <code>&lt;container&gt;</code>", parse_mode="HTML")
            return
        await snooze_db.unsnooze_container(name)
        await message.reply(f"🔔 <b>Alerts resumed for <code>{name}</code></b>", parse_mode="HTML")

    @dp.message(Command("ignored"))
    async def cmd_ignored(message: Message):
        entries = await snooze_db.list_snoozed()
        if not entries:
            await message.reply("✅ <b>No containers are muted.</b>", parse_mode="HTML")
            return
        lines = ["🔇 <b>Muted Containers</b>\n━━━━━━━━━━━━━━━━━━━━━"]
        for e in entries:
            lines.append(f"• <code>{e['name']}</code> — {e['remaining']}")
        await message.reply("\n".join(lines), parse_mode="HTML")

    # ─── /renewals ────────────────────────────────────────────────────────────

    @dp.message(Command("renewals"))
    async def cmd_renewals(message: Message):
        await _send_renewals_page(message, 1)

    async def _send_renewals_page(message: Message, page: int):
        all_r = await ren_db.list_renewals(status="pending")
        total = len(all_r)
        start = (page - 1) * PER_PAGE
        page_items = all_r[start : start + PER_PAGE]
        total_pages = max(1, -(-total // PER_PAGE))
        text = tpl.format_renewals_page(page_items, page, total)
        pagination = kb.get_pagination_keyboard("renewals", page, total_pages)
        await message.reply(text, reply_markup=pagination, parse_mode="HTML")

    # ─── /addrenew (multi-step FSM) ───────────────────────────────────────────

    @dp.message(Command("addrenew"))
    async def cmd_addrenew_start(message: Message, state: FSMContext):
        await state.set_state(AddRenewalForm.name)
        await message.reply(
            "💳 <b>Add Renewal</b> (Step 1/5)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "What is the <b>name</b> of this renewal?\n\n<i>Example: OVH VPS Node-01</i>",
            parse_mode="HTML",
        )

    @dp.message(AddRenewalForm.name)
    async def addrenew_name(message: Message, state: FSMContext):
        await state.update_data(name=message.text)
        await state.set_state(AddRenewalForm.category)
        await message.reply(
            "💳 <b>Add Renewal</b> (Step 2/5)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Category? Reply with one of:\n"
            "<code>vps</code>  <code>domain</code>  <code>ssl</code>  <code>software</code>  <code>other</code>",
            parse_mode="HTML",
        )

    @dp.message(AddRenewalForm.category)
    async def addrenew_category(message: Message, state: FSMContext):
        cat = (message.text or "other").lower().strip()
        if cat not in ("vps", "domain", "ssl", "software", "other"):
            cat = "other"
        await state.update_data(category=cat)
        await state.set_state(AddRenewalForm.due_date)
        await message.reply(
            "💳 <b>Add Renewal</b> (Step 3/5)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Due date? Format: <code>YYYY-MM-DD</code>\n<i>Example: 2026-11-15</i>",
            parse_mode="HTML",
        )

    @dp.message(AddRenewalForm.due_date)
    async def addrenew_due_date(message: Message, state: FSMContext):
        raw = (message.text or "").strip()
        try:
            from datetime import date as _date

            _date.fromisoformat(raw)  # validate
        except ValueError:
            await message.reply("❌ Invalid date. Please use <code>YYYY-MM-DD</code> format.", parse_mode="HTML")
            return
        await state.update_data(due_date=raw)
        await state.set_state(AddRenewalForm.amount)
        await message.reply(
            "💳 <b>Add Renewal</b> (Step 4/5)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Amount in INR? (or type <code>skip</code> to leave blank)",
            parse_mode="HTML",
        )

    @dp.message(AddRenewalForm.amount)
    async def addrenew_amount(message: Message, state: FSMContext):
        raw = (message.text or "").strip()
        amount: float | None = None
        if raw.lower() != "skip":
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                await message.reply("❌ Enter a number or <code>skip</code>.", parse_mode="HTML")
                return
        await state.update_data(amount=amount)
        await state.set_state(AddRenewalForm.recurrence)
        await message.reply(
            "💳 <b>Add Renewal</b> (Step 5/5)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Recurrence? Reply:\n"
            "<code>none</code>  <code>monthly</code>  <code>yearly</code>",
            parse_mode="HTML",
        )

    @dp.message(AddRenewalForm.recurrence)
    async def addrenew_recurrence(message: Message, state: FSMContext):
        recur = (message.text or "none").lower().strip()
        if recur not in ("none", "monthly", "yearly"):
            recur = "none"
        data = await state.get_data()
        await state.clear()
        renewal_id = await ren_db.add_renewal(
            name=data["name"],
            category=data["category"],
            due_date=data["due_date"],
            amount=data.get("amount"),
            recurrence=recur,
        )
        recur_label = f" · {recur.capitalize()}" if recur != "none" else ""
        await message.reply(
            f"✅ <b>Renewal Added</b> #{renewal_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 {data['name']}\n"
            f"📅 Due: {data['due_date']}{recur_label}",
            parse_mode="HTML",
        )

    # ─── /probes ──────────────────────────────────────────────────────────────

    @dp.message(Command("probes"))
    async def cmd_probes(message: Message):
        endpoints = await ep_db.list_endpoints(enabled_only=True)
        yaml_eps = [
            {
                "name": e.name,
                "url": e.url,
                "expected_status": e.expected_status,
                "timeout_seconds": e.timeout_seconds,
                "id": None,
            }
            for e in settings.monitoring.http_endpoints
        ]
        ep_names = {ep["name"] for ep in endpoints}
        for yep in yaml_eps:
            if yep["name"] not in ep_names:
                endpoints.append(yep)

        if not endpoints:
            await message.reply(
                "🌐 <b>No HTTP endpoints configured.</b>\n\nUse /addprobe to add one.", parse_mode="HTML"
            )
            return

        status_msg = await message.reply("🔄 <i>Checking endpoints...</i>", parse_mode="HTML")
        tasks = [probe_http_endpoint(ep["name"], ep["url"]) for ep in endpoints]
        import asyncio

        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_results = [r for r in results if not isinstance(r, Exception)]
        text = tpl.format_probes_list(endpoints, valid_results)
        await status_msg.edit_text(text, parse_mode="HTML")

    # ─── /addprobe (multi-step) ───────────────────────────────────────────────

    @dp.message(Command("addprobe"))
    async def cmd_addprobe_start(message: Message, state: FSMContext):
        await state.set_state(AddProbeForm.name)
        await message.reply(
            "🌐 <b>Add HTTP Probe</b> (Step 1/3)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Give this endpoint a <b>name</b>:\n<i>Example: GNS API Health</i>",
            parse_mode="HTML",
        )

    @dp.message(AddProbeForm.name)
    async def addprobe_name(message: Message, state: FSMContext):
        await state.update_data(name=message.text)
        await state.set_state(AddProbeForm.url)
        await message.reply(
            "🌐 <b>Add HTTP Probe</b> (Step 2/3)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Enter the full <b>URL</b>:\n<i>Example: https://api.sendrin.com/health</i>",
            parse_mode="HTML",
        )

    @dp.message(AddProbeForm.url)
    async def addprobe_url(message: Message, state: FSMContext):
        url = (message.text or "").strip()
        if not url.startswith("http"):
            await message.reply(
                "❌ URL must start with <code>http://</code> or <code>https://</code>", parse_mode="HTML"
            )
            return
        await state.update_data(url=url)
        await state.set_state(AddProbeForm.expected_status)
        await message.reply(
            "🌐 <b>Add HTTP Probe</b> (Step 3/3)\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Expected HTTP status code? (or type <code>skip</code> for 200)",
            parse_mode="HTML",
        )

    @dp.message(AddProbeForm.expected_status)
    async def addprobe_status(message: Message, state: FSMContext):
        raw = (message.text or "").strip()
        status_code = 200
        if raw.lower() != "skip":
            try:
                status_code = int(raw)
            except ValueError:
                await message.reply("❌ Enter a number like <code>200</code> or <code>skip</code>.", parse_mode="HTML")
                return
        data = await state.get_data()
        await state.clear()
        ep_id = await ep_db.add_endpoint(data["name"], data["url"], status_code)
        await message.reply(
            f"✅ <b>Endpoint Added</b> #{ep_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 {data['name']}\n"
            f"🔗 <code>{data['url']}</code>\n"
            f"📊 Expected: HTTP {status_code}",
            parse_mode="HTML",
        )

    # ─── /rmprobe ─────────────────────────────────────────────────────────────

    @dp.message(Command("rmprobe"))
    async def cmd_rmprobe(message: Message, command: CommandObject):
        arg = (command.args or "").strip()
        if not arg.isdigit():
            await message.reply(
                "Usage: /rmprobe <code>&lt;endpoint_id&gt;</code>\nFind IDs with /probes", parse_mode="HTML"
            )
            return
        ep_id = int(arg)
        await ep_db.remove_endpoint(ep_id)
        await message.reply(
            f"🔕 <b>Endpoint #{ep_id} disabled.</b>\n<i>It will not be re-added on restart.</i>", parse_mode="HTML"
        )

    # ─── /incidents ───────────────────────────────────────────────────────────

    @dp.message(Command("incidents"))
    async def cmd_incidents(message: Message):
        await _send_incidents_page(message, 1)

    async def _send_incidents_page(message: Message, page: int):
        total = await inc_db.count_open_incidents()
        offset = (page - 1) * PER_PAGE
        incidents = await inc_db.list_open_incidents(limit=PER_PAGE, offset=offset)
        total_pages = max(1, -(-total // PER_PAGE))
        text = tpl.format_incidents_page(incidents, page, total)
        pagination = kb.get_pagination_keyboard("incidents", page, total_pages)
        await message.reply(text, reply_markup=pagination, parse_mode="HTML")

    # ─── /setchat ─────────────────────────────────────────────────────────────

    @dp.message(Command("setchat"))
    async def cmd_setchat(message: Message, command: CommandObject):
        chat_id = (command.args or "").strip()
        if not chat_id:
            current = await get_setting("alert_chat_id", settings.telegram_alert_chat_id)
            await message.reply(
                f"⚙️ <b>Current alert chat ID:</b> <code>{current}</code>\n\n"
                f"To change: /setchat <code>&lt;new_chat_id&gt;</code>",
                parse_mode="HTML",
            )
            return
        await set_setting("alert_chat_id", chat_id)
        # Update the live channel so alerts go to the new ID without restart
        if channel is not None and hasattr(channel, "update_chat_id"):
            channel.update_chat_id(chat_id)
        await message.reply(
            f"✅ <b>Alert chat ID updated</b>\n<code>{chat_id}</code>\n\n"
            f"<i>Restart not required — takes effect immediately.</i>",
            parse_mode="HTML",
        )

    # ─── /settings ────────────────────────────────────────────────────────────

    @dp.message(Command("settings"))
    async def cmd_settings(message: Message):
        from opspilot.db.store import get_all_settings

        all_s = await get_all_settings()
        lines = ["⚙️ <b>Runtime Settings</b>\n━━━━━━━━━━━━━━━━━━━━━"]
        if not all_s:
            lines.append("<i>No custom settings. Using defaults from config.yaml</i>")
        for k, v in all_s.items():
            lines.append(f"• <code>{k}</code> = <code>{v}</code>")
        await message.reply("\n".join(lines), parse_mode="HTML")

    # ─── /ask ─────────────────────────────────────────────────────────────────

    @dp.message(Command("ask"))
    async def cmd_ask(message: Message, command: CommandObject):
        query = (command.args or "").strip()
        if not query:
            await message.reply(
                "🤖 <b>Ask OpsPilot AI</b>\n\nUsage: /ask <code>&lt;question&gt;</code>\n"
                "<i>Example: /ask why is memory usage high?</i>",
                parse_mode="HTML",
            )
            return
        if settings.ai.provider != "ollama" and (not settings.ai.api_key or settings.ai.api_key.startswith("sk-...")):
            await message.reply(
                "⚠️ <b>AI Copilot not configured.</b>\n\n"
                "Set <code>AI_API_KEY</code> in <code>.env</code> or use <code>AI_PROVIDER=ollama</code>.",
                parse_mode="HTML",
            )
            return
        status_msg = await message.reply("🤖 <i>Analysing infrastructure context...</i>", parse_mode="HTML")
        context = {
            "metrics": collect_system_metrics().model_dump(),
            "containers": [c.model_dump() for c in collect_docker_statuses()],
            "ssl": [check_domain_ssl(d).model_dump() for d in settings.monitoring.ssl_domains[:3]],
        }
        answer = await copilot.ask(query, context)
        audit.record_action(message.from_user.id if message.from_user else 0, "ai_ask", query, "SUCCESS")
        await status_msg.edit_text(
            f"🤖 <b>OpsPilot AI Analysis</b>\n━━━━━━━━━━━━━━━━━━━━━\n{answer}",
            parse_mode="HTML",
        )

    # ─── Callback: Container actions ──────────────────────────────────────────

    @dp.callback_query(F.data.startswith("act:"))
    async def callback_act(query: CallbackQuery):
        if not query.data or not query.message:
            return
        _, action, container_name = query.data.split(":", 2)

        if action == "logs":
            result = await executor.run_command(["docker", "logs", "--tail", "40", container_name])
            output = (result.get("stdout") or "") + (result.get("stderr") or "")
            text = (
                f"📋 <b>Logs: {container_name}</b>\n<pre>{output[:3500]}</pre>"
                if output
                else f"📋 <b>{container_name}</b>: No logs."
            )
            await query.message.reply(text, parse_mode="HTML")

        elif action == "restart":
            await query.message.reply(
                f"⚠️ <b>Confirm Restart</b>\nRestart <code>{container_name}</code>?",
                reply_markup=kb.get_confirmation_keyboard("restart", container_name),
                parse_mode="HTML",
            )

        elif action == "ignore":
            await query.message.reply(
                f"🔇 <b>Snooze alerts for <code>{container_name}</code></b>?\nChoose duration:",
                reply_markup=kb.get_ignore_duration_keyboard(container_name),
                parse_mode="HTML",
            )
        await query.answer()

    # ─── Callback: Snooze container ───────────────────────────────────────────

    @dp.callback_query(F.data.startswith("snooze:"))
    async def callback_snooze(query: CallbackQuery):
        if not query.data:
            return
        parts = query.data.split(":")
        container_name = parts[1]
        duration = parts[2] if len(parts) > 2 else "forever"
        exp_dt = _parse_duration_to_dt(duration)
        await snooze_db.snooze_container(container_name, exp_dt)
        until = exp_dt.strftime("%d %b %Y %H:%M UTC") if exp_dt else "indefinitely"
        if query.message:
            await query.message.edit_text(
                f"🔇 <b>Alerts muted for <code>{container_name}</code></b>\n⏳ Until: {until}",
                parse_mode="HTML",
            )
        await query.answer("Snoozed ✓")

    # ─── Callback: Confirm / Cancel ───────────────────────────────────────────

    @dp.callback_query(F.data.startswith("confirm:"))
    async def callback_confirm(query: CallbackQuery):
        if not query.data or not query.message:
            return
        _, action, target = query.data.split(":", 2)
        user_id = query.from_user.id if query.from_user else 0

        if action == "restart":
            status_msg = await query.message.reply("🔄 <i>Restarting container...</i>", parse_mode="HTML")
            result = await executor.run_command(["docker", "restart", target])
            if result.get("returncode") == 0:
                audit.record_action(user_id, "restart", target, "SUCCESS")
                await status_msg.edit_text(f"✅ <b>Container Restarted</b>\n<code>{target}</code>", parse_mode="HTML")
            else:
                await status_msg.edit_text(
                    f"❌ <b>Restart Failed</b>\n<code>{result.get('stderr', 'unknown error')}</code>",
                    parse_mode="HTML",
                )
        elif action == "clean":
            status_msg = await query.message.reply("🧹 <i>Pruning Docker cache...</i>", parse_mode="HTML")
            result = await executor.run_command(["docker", "system", "prune", "-f"])
            if result.get("returncode") == 0:
                await status_msg.edit_text(
                    "🧹 <b>Docker Cache Pruned</b>\n✅ Dangling images and build cache cleared.", parse_mode="HTML"
                )
            else:
                await status_msg.edit_text(
                    f"❌ Cleanup failed: <code>{result.get('stderr', 'unknown')}</code>", parse_mode="HTML"
                )
        await query.answer()

    @dp.callback_query(F.data.startswith("cancel:"))
    async def callback_cancel(query: CallbackQuery):
        if query.message:
            await query.message.edit_text("❌ <b>Action cancelled.</b>", parse_mode="HTML")
        await query.answer()

    # ─── Callback: Incidents ──────────────────────────────────────────────────

    @dp.callback_query(F.data.startswith("inc:"))
    async def callback_incident(query: CallbackQuery):
        if not query.data:
            return
        parts = query.data.split(":")
        action = parts[1]
        inc_id = int(parts[2])

        if action == "resolve":
            await inc_db.resolve_incident_by_id(inc_id)
            if query.message:
                await query.message.edit_text(f"✅ <b>Incident #{inc_id} resolved.</b>", parse_mode="HTML")
        elif action == "snooze":
            duration = parts[3] if len(parts) > 3 else "1h"
            exp_dt = _parse_duration_to_dt(duration)
            if exp_dt:
                await inc_db.snooze_incident(inc_id, exp_dt)
            if query.message:
                await query.message.edit_text(
                    f"🔇 <b>Incident #{inc_id} snoozed until</b> {_snooze_until_str(duration)}",
                    parse_mode="HTML",
                )
        elif action == "recheck":
            await query.answer("🔄 Recheck triggered on next probe cycle.", show_alert=True)
            return
        await query.answer()

    # ─── Callback: Renewals ───────────────────────────────────────────────────

    @dp.callback_query(F.data.startswith("ren:"))
    async def callback_renewal(query: CallbackQuery):
        if not query.data:
            return
        parts = query.data.split(":")
        action = parts[1]
        renewal_id = int(parts[2])

        renewal = await ren_db.get_renewal(renewal_id)
        if not renewal:
            await query.answer("Renewal not found.", show_alert=True)
            return

        if action == "paid":
            next_r = await ren_db.mark_paid(renewal_id)
            text = tpl.renewal_paid_confirmation(renewal, next_r)
            if query.message:
                await query.message.edit_text(text, parse_mode="HTML")

        elif action == "snooze":
            duration = parts[3] if len(parts) > 3 else "1d"
            exp_dt = _parse_duration_to_dt(duration)
            if exp_dt:
                await ren_db.snooze_renewal(renewal_id, exp_dt)
            if query.message:
                await query.message.edit_text(
                    tpl.renewal_snoozed(renewal, _snooze_until_str(duration)),
                    parse_mode="HTML",
                )
        await query.answer()

    # ─── Callback: cmd menu ───────────────────────────────────────────────────

    @dp.callback_query(F.data.startswith("cmd:"))
    async def callback_menu(query: CallbackQuery):
        if not query.data or not query.message:
            return
        parts = query.data.split(":")
        cmd = parts[1]

        if cmd == "status":
            m = collect_system_metrics()
            await query.message.reply(tpl.system_status(m, settings.server_name), parse_mode="HTML")

        elif cmd == "ps":
            containers = collect_docker_statuses()
            if not containers:
                await query.message.reply("🐳 <b>No containers found.</b>", parse_mode="HTML")
            else:
                lines = [f"🐳 <b>Docker Containers ({len(containers)})</b>\n━━━━━━━━━━━━━━━━━━━━━"]
                for c in containers:
                    snoozed = await snooze_db.is_snoozed(c.name)
                    icon = "🟢" if c.status == "running" else "🔴"
                    muted = " 🔇" if snoozed else ""
                    lines.append(f"{icon} <code>{c.name}</code>{muted}")
                await query.message.reply("\n".join(lines), parse_mode="HTML")

        elif cmd == "disk":
            m = collect_system_metrics()
            bar = "█" * int(m.disk_percent / 10) + "░" * (10 - int(m.disk_percent / 10))
            text = (
                f"💾 <b>Disk — {settings.server_name}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"[{bar}] <b>{m.disk_percent:.1f}%</b>\n"
                f"• Total: <b>{m.disk_total_gb} GB</b>\n"
                f"• Used:  <b>{m.disk_used_gb} GB</b>\n"
                f"• Free:  <b>{m.disk_free_gb} GB</b>"
            )
            await query.message.reply(text, parse_mode="HTML")

        elif cmd == "clean":
            await query.message.reply(
                f"⚠️ <b>Prune Docker Cache</b>\nClean dangling images and build cache on <b>{settings.server_name}</b>?",
                reply_markup=kb.get_confirmation_keyboard("clean", "docker_cache"),
                parse_mode="HTML",
            )

        elif cmd == "ssl":
            if not settings.monitoring.ssl_domains:
                await query.message.reply("🔒 <b>No SSL domains configured.</b>", parse_mode="HTML")
            else:
                lines = ["🔒 <b>SSL Certificate Status</b>\n━━━━━━━━━━━━━━━━━━━━━"]
                for d in settings.monitoring.ssl_domains:
                    res = check_domain_ssl(d)
                    icon = "🟢" if res.days_remaining > 14 else "🟡" if res.days_remaining > 0 else "🔴"
                    lines.append(f"{icon} <code>{d}</code> — <b>{res.days_remaining}d</b> left ({res.expires_at})")
                await query.message.reply("\n".join(lines), parse_mode="HTML")

        elif cmd == "probes":
            endpoints = await ep_db.list_endpoints(enabled_only=True)
            if not endpoints:
                await query.message.reply("🌐 <b>No endpoints configured.</b>\nUse /addprobe", parse_mode="HTML")
            else:
                import asyncio as _a

                results = await _a.gather(
                    *[probe_http_endpoint(ep["name"], ep["url"]) for ep in endpoints], return_exceptions=True
                )
                valid = [r for r in results if not isinstance(r, Exception)]
                await query.message.reply(tpl.format_probes_list(endpoints, valid), parse_mode="HTML")

        elif cmd == "renewals":
            page = int(parts[2]) if len(parts) > 2 else 1
            all_r = await ren_db.list_renewals(status="pending")
            total = len(all_r)
            start = (page - 1) * PER_PAGE
            page_items = all_r[start : start + PER_PAGE]
            total_pages = max(1, -(-total // PER_PAGE))
            text = tpl.format_renewals_page(page_items, page, total)
            pagination = kb.get_pagination_keyboard("renewals", page, total_pages)
            await query.message.reply(text, reply_markup=pagination, parse_mode="HTML")

        elif cmd == "incidents":
            page = int(parts[2]) if len(parts) > 2 else 1
            total = await inc_db.count_open_incidents()
            offset = (page - 1) * PER_PAGE
            incidents = await inc_db.list_open_incidents(limit=PER_PAGE, offset=offset)
            total_pages = max(1, -(-total // PER_PAGE))
            text = tpl.format_incidents_page(incidents, page, total)
            pagination = kb.get_pagination_keyboard("incidents", page, total_pages)
            await query.message.reply(text, reply_markup=pagination, parse_mode="HTML")

        elif cmd == "ask":
            await query.message.reply(
                "🤖 <b>Ask OpsPilot AI</b>\n\nType /ask <code>&lt;your question&gt;</code>\n"
                "<i>Example: /ask why is memory usage high?</i>",
                parse_mode="HTML",
            )

        await query.answer()

    # ─── Callback: Endpoint remove ────────────────────────────────────────────

    @dp.callback_query(F.data.startswith("ep:"))
    async def callback_endpoint(query: CallbackQuery):
        if not query.data:
            return
        parts = query.data.split(":")
        action = parts[1]
        ep_id = int(parts[2])
        if action == "remove":
            await ep_db.remove_endpoint(ep_id)
            if query.message:
                await query.message.edit_text(
                    f"🔕 <b>Endpoint #{ep_id} disabled.</b>\n<i>It will not be re-added on restart.</i>",
                    parse_mode="HTML",
                )
        elif action == "cancel":
            if query.message:
                await query.message.edit_text("❌ <b>Cancelled.</b>", parse_mode="HTML")
        await query.answer()

    return bot, dp
