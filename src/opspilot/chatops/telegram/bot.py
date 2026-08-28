import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from opspilot.ai.copilot import OpsCopilot
from opspilot.ai.provider import AIProvider
from opspilot.chatops.telegram.keyboards import get_confirmation_keyboard, get_main_menu_keyboard
from opspilot.config import Settings
from opspilot.core.audit import AuditLogger
from opspilot.core.executor import SafeOperationExecutor
from opspilot.core.security import AccessController
from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.ssl import check_domain_ssl
from opspilot.monitor.system import collect_system_metrics

logger = logging.getLogger("opspilot.telegram")


def create_bot_app(settings: Settings):
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    access = AccessController(settings.allowed_users, auth_mode=settings.auth_mode)
    audit = AuditLogger()
    executor = SafeOperationExecutor()
    ai_provider = AIProvider(
        provider=settings.ai.provider,
        model=settings.ai.model,
        api_key=settings.ai.api_key,
        base_url=settings.ai.base_url,
    )
    copilot = OpsCopilot(ai_provider)

    @dp.message(F.from_user.id)
    async def auth_middleware(message: Message, handler):
        user_id = message.from_user.id
        if not access.is_authorized(user_id):
            await message.reply(f"⛔ Unauthorized. Your Telegram User ID is `{user_id}`.", parse_mode="Markdown")
            audit.record_action(user_id, "unauthorized_access", "bot", "BLOCKED")
            return
        return await handler(message)

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        text = f"👋 *Welcome to OpsPilot*\nInfrastructure Command Center for *{settings.server_name}*\n\nUse the menu below or type /status, /ps, /logs, /restart, /ask."
        await message.reply(text, reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")

    @dp.message(Command("status"))
    async def cmd_status(message: Message):
        m = collect_system_metrics()
        text = (
            f"🟢 *Server Status: {settings.server_name}*\n\n"
            f"*CPU:* {m.cpu_percent}% ({m.cpu_count} cores)\n"
            f"*Memory:* {m.ram_percent}% ({m.ram_used_gb} / {m.ram_total_gb} GB)\n"
            f"*Disk (/):* {m.disk_percent}% ({m.disk_used_gb} / {m.disk_total_gb} GB, {m.disk_free_gb} GB free)\n"
            f"*Load Average:* {m.load_avg[0]}, {m.load_avg[1]}, {m.load_avg[2]}\n"
            f"*Uptime:* {m.uptime_human}"
        )
        audit.record_action(message.from_user.id, "status", "system", "SUCCESS")
        await message.reply(text, parse_mode="Markdown")

    @dp.message(Command("ps"))
    async def cmd_ps(message: Message):
        containers = collect_docker_statuses()
        if not containers:
            await message.reply("🐳 No active Docker containers found.")
            return
        lines = [f"🐳 *Docker Containers ({len(containers)})*"]
        for c in containers:
            status_icon = "🟢" if c.status == "running" else "🔴"
            health_str = f" [{c.health}]" if c.health != "none" else ""
            lines.append(f"{status_icon} `{c.name}` ({c.status}{health_str})")
        audit.record_action(message.from_user.id, "ps", "docker", "SUCCESS")
        await message.reply("\n".join(lines), parse_mode="Markdown")

    @dp.message(Command("logs"))
    async def cmd_logs(message: Message):
        args = message.text.split()[1:]
        if not args:
            await message.reply(
                "Usage: `/logs <container_name> [lines]`\nExample: `/logs growixa-api-1 50`", parse_mode="Markdown"
            )
            return
        container = args[0]
        tail = int(args[1]) if len(args) > 1 and args[1].isdigit() else 40
        logs = await executor.get_container_logs(container, tail=tail)
        audit.record_action(message.from_user.id, "logs", container, "SUCCESS")
        if len(logs) > 3800:
            logs = logs[-3800:]
        await message.reply(f"📋 *Logs for {container} (tail {tail})*:\n```\n{logs}\n```", parse_mode="Markdown")

    @dp.message(Command("restart"))
    async def cmd_restart(message: Message):
        args = message.text.split()[1:]
        if not args:
            await message.reply("Usage: `/restart <container_name>`", parse_mode="Markdown")
            return
        container = args[0]
        kb = get_confirmation_keyboard("restart", container)
        await message.reply(
            f"⚠️ *Confirmation Required*\nAre you sure you want to restart container `{container}` on *{settings.server_name}*?",
            reply_markup=kb,
            parse_mode="Markdown",
        )

    @dp.callback_query(F.data.startswith("confirm:"))
    async def callback_confirm(query: CallbackQuery):
        _, action, target = query.data.split(":")
        user_id = query.from_user.id
        if not access.is_authorized(user_id):
            await query.answer("Unauthorized", show_alert=True)
            return

        if action == "restart":
            await query.message.edit_text(f"🔄 Restarting `{target}`...", parse_mode="Markdown")
            res = await executor.restart_container(target)
            status = "SUCCESS" if res["success"] else "FAILED"
            audit.record_action(user_id, "restart", target, status)
            await query.message.edit_text(f"{'✅' if res['success'] else '❌'} {res['message']}", parse_mode="Markdown")

    @dp.callback_query(F.data.startswith("cancel:"))
    async def callback_cancel(query: CallbackQuery):
        await query.message.edit_text("❌ Action cancelled by user.")

    @dp.message(Command("ask"))
    async def cmd_ask(message: Message):
        query = message.text[5:].strip()
        if not query:
            await message.reply(
                "Usage: `/ask <question about servers or containers>`\nExample: `/ask why is API response slow?`",
                parse_mode="Markdown",
            )
            return

        status_msg = await message.reply(
            "🤖 *OpsPilot AI is analyzing infrastructure context...*", parse_mode="Markdown"
        )
        context = {
            "metrics": collect_system_metrics().model_dump(),
            "containers": [c.model_dump() for c in collect_docker_statuses()],
            "ssl": [check_domain_ssl(d).model_dump() for d in settings.monitoring.ssl_domains[:3]],
        }
        answer = await copilot.ask(query, context)
        audit.record_action(message.from_user.id, "ai_ask", query, "SUCCESS")
        await status_msg.edit_text(f"🤖 *OpsPilot AI Analysis*:\n\n{answer}", parse_mode="Markdown")

    return bot, dp
