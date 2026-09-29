"""
Rich, attractive Telegram HTML message templates for OpsPilot 2.0.

All messages use HTML parse_mode. Consistent visual language:
  🔴 Critical   🟡 Warning   🟢 Healthy / OK   🔵 Info
  ━━━━━━━━━━━  section dividers
  <code>      monospace for names, URLs, commands
  <b>         bold for key values
"""

from __future__ import annotations

from datetime import UTC, date, datetime

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _now_str() -> str:
    return datetime.now(UTC).strftime("%d %b %Y · %H:%M UTC")


def _days_badge(days: int) -> str:
    if days < 0:
        return f"🔴 <b>OVERDUE by {abs(days)} day{'s' if abs(days) != 1 else ''}</b>"
    if days == 0:
        return "🔴 <b>DUE TODAY</b>"
    if days <= 3:
        return f"🟡 <b>{days} day{'s' if days != 1 else ''} left</b>"
    return f"🟢 <b>{days} days left</b>"


def _severity_icon(severity: str) -> str:
    return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "🟡")


def _category_icon(category: str) -> str:
    return {
        "vps": "🖥️",
        "domain": "🌐",
        "ssl": "🔒",
        "software": "💿",
        "other": "📦",
    }.get(category, "📦")


def _fmt_amount(amount: float | None, currency: str) -> str:
    if amount is None:
        return "—"
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    return f"{sym}{amount:,.0f}"


# ─── Container / Docker Alerts ────────────────────────────────────────────────


def container_alert(container: str, status: str, health: str, server: str, alert_count: int = 1) -> str:
    repeat = f"\n⚠️ <i>Alert #{alert_count} — first seen earlier</i>" if alert_count > 1 else ""
    return (
        f"🔴 <b>Container Down</b>{repeat}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Container:</b> <code>{container}</code>\n"
        f"🖥️ <b>Server:</b> <code>{server}</code>\n"
        f"📊 <b>Status:</b> <code>{status}</code>  |  Health: <code>{health}</code>\n"
        f"🕐 <b>Detected:</b> {_now_str()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Use the buttons below to act or snooze alerts.</i>"
    )


def container_recovered(container: str, server: str) -> str:
    return (
        f"✅ <b>Container Recovered</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Container:</b> <code>{container}</code>\n"
        f"🖥️ <b>Server:</b> <code>{server}</code>\n"
        f"🕐 <b>Resolved:</b> {_now_str()}"
    )


# ─── HTTP Probe Alerts ────────────────────────────────────────────────────────


def probe_down(name: str, url: str, status_code: int | None, error: str | None, alert_count: int = 1) -> str:
    repeat = f"\n⚠️ <i>Alert #{alert_count} — still unreachable</i>" if alert_count > 1 else ""
    detail = f"HTTP <code>{status_code}</code>" if status_code else f"<i>{error or 'no response'}</i>"
    return (
        f"🔴 <b>Endpoint Down</b>{repeat}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Name:</b> {name}\n"
        f"🔗 <b>URL:</b> <code>{url}</code>\n"
        f"📊 <b>Response:</b> {detail}\n"
        f"🕐 <b>Detected:</b> {_now_str()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Use the buttons below to resolve or snooze.</i>"
    )


def probe_recovered(name: str, url: str, latency_ms: float) -> str:
    return (
        f"✅ <b>Endpoint Recovered</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Name:</b> {name}\n"
        f"🔗 <b>URL:</b> <code>{url}</code>\n"
        f"⚡ <b>Latency:</b> {latency_ms:.0f} ms\n"
        f"🕐 <b>Resolved:</b> {_now_str()}"
    )


# ─── SSL Alerts ───────────────────────────────────────────────────────────────


def ssl_expiring(domain: str, days_remaining: int, expires_at: str) -> str:
    badge = _days_badge(days_remaining)
    return (
        f"🔒 <b>SSL Certificate Expiring</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Domain:</b> <code>{domain}</code>\n"
        f"📅 <b>Expires:</b> {expires_at}\n"
        f"⏳ {badge}\n"
        f"🕐 <b>Checked:</b> {_now_str()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Renew immediately to avoid service disruption.</i>"
    )


# ─── Disk / System Alerts ─────────────────────────────────────────────────────


def disk_critical(server: str, disk_pct: float, free_gb: float) -> str:
    bar_filled = int(disk_pct / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    return (
        f"🔴 <b>Critical Disk Usage</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ <b>Server:</b> <code>{server}</code>\n"
        f"💾 <b>Disk Usage:</b> [{bar}] <b>{disk_pct:.1f}%</b>\n"
        f"📦 <b>Free Space:</b> {free_gb:.1f} GB remaining\n"
        f"🕐 <b>Detected:</b> {_now_str()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Run /clean to prune Docker cache.</i>"
    )


# ─── Renewal / Billing Alerts ─────────────────────────────────────────────────


def renewal_reminder(renewal: dict) -> str:
    due = date.fromisoformat(renewal["due_date"])
    days = (due - date.today()).days
    badge = _days_badge(days)
    icon = _category_icon(renewal["category"])
    amount_str = _fmt_amount(renewal.get("amount"), renewal.get("currency", "INR"))
    recur_str = ""
    if renewal.get("recurrence", "none") != "none":
        recur_str = f"\n🔄 <b>Recurrence:</b> {renewal['recurrence'].capitalize()}"
    notes_str = f"\n📝 <b>Notes:</b> <i>{renewal['notes']}</i>" if renewal.get("notes") else ""
    overdue_note = "\n\n🚨 <i>This is overdue! Mark it paid or update the due date.</i>" if days < 0 else ""
    return (
        f"{icon} <b>Renewal Due</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Name:</b> {renewal['name']}\n"
        f"🏷️ <b>Category:</b> {renewal['category'].upper()}\n"
        f"📅 <b>Due Date:</b> {renewal['due_date']}\n"
        f"⏳ {badge}\n"
        f"💰 <b>Amount:</b> {amount_str}"
        f"{recur_str}{notes_str}"
        f"\n━━━━━━━━━━━━━━━━━━━━━"
        f"{overdue_note}"
    )


def renewal_paid_confirmation(renewal: dict, next_renewal: dict | None = None) -> str:
    icon = _category_icon(renewal["category"])
    next_str = ""
    if next_renewal:
        next_str = f"\n\n🔄 <b>Next renewal auto-scheduled:</b>\n   📅 {next_renewal['due_date']}"
    return (
        f"✅ <b>Marked as Paid</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{icon} <b>{renewal['name']}</b>\n"
        f"📅 Due: {renewal['due_date']}{next_str}\n"
        f"🕐 {_now_str()}"
    )


def renewal_snoozed(renewal: dict, until_str: str) -> str:
    return (
        f"🔇 <b>Reminder Snoozed</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>{renewal['name']}</b>\n"
        f"⏳ <b>Next reminder:</b> {until_str}"
    )


# ─── Incident list (for /incidents command) ───────────────────────────────────


def format_incidents_page(incidents: list[dict], page: int, total: int, per_page: int = 5) -> str:
    if not incidents:
        return "✅ <b>No open incidents.</b>\n\n<i>All systems are operating normally.</i>"

    total_pages = max(1, -(-total // per_page))  # ceil div
    lines = [f"📋 <b>Open Incidents</b>  <i>(page {page}/{total_pages})</i>\n━━━━━━━━━━━━━━━━━━━━━"]
    for inc in incidents:
        sev_icon = _severity_icon(inc["severity"])
        repeat = f" · ×{inc['alert_count']}" if inc["alert_count"] > 1 else ""
        lines.append(
            f"\n{sev_icon} <b>{inc['title']}</b>{repeat}\n"
            f"   🎯 <code>{inc['target']}</code>  |  #{inc['id']}\n"
            f"   🕐 {inc['created_at'][:16]}"
        )
    return "\n".join(lines)


# ─── Renewals list (for /renewals command) ────────────────────────────────────


def format_renewals_page(renewals: list[dict], page: int, total: int, per_page: int = 5) -> str:
    if not renewals:
        return "✅ <b>No pending renewals.</b>\n\n<i>Nothing is due soon.</i>"

    total_pages = max(1, -(-total // per_page))
    lines = [f"💳 <b>Pending Renewals</b>  <i>(page {page}/{total_pages})</i>\n━━━━━━━━━━━━━━━━━━━━━"]
    for r in renewals:
        due = date.fromisoformat(r["due_date"])
        days = (due - date.today()).days
        icon = _category_icon(r["category"])
        amount_str = _fmt_amount(r.get("amount"), r.get("currency", "INR"))
        if days < 0:
            day_str = f"🔴 {abs(days)}d overdue"
        elif days == 0:
            day_str = "🔴 TODAY"
        elif days <= 3:
            day_str = f"🟡 {days}d"
        else:
            day_str = f"🟢 {days}d"
        lines.append(
            f"\n{icon} <b>{r['name']}</b>  [{day_str}]\n   💰 {amount_str}  |  📅 {r['due_date']}  |  #{r['id']}"
        )
    return "\n".join(lines)


# ─── Probes list ──────────────────────────────────────────────────────────────


def format_probes_list(probes: list[dict], results: list) -> str:
    if not probes:
        return "🌐 <b>No HTTP endpoints configured.</b>\n\nUse /addprobe to add one."

    lines = ["🌐 <b>HTTP Endpoint Status</b>\n━━━━━━━━━━━━━━━━━━━━━"]
    result_map = {r.name: r for r in results}
    for ep in probes:
        r = result_map.get(ep["name"])
        if r is None:
            icon, status = "⬜", "not checked"
        elif r.is_healthy:
            icon, status = "🟢", f"{r.status_code} · {r.latency_ms:.0f}ms"
        else:
            icon, status = "🔴", f"{r.status_code or r.error}"
        lines.append(f"\n{icon} <b>{ep['name']}</b>  #{ep['id']}\n   🔗 <code>{ep['url']}</code>\n   📊 {status}")
    return "\n".join(lines)


# ─── System status ────────────────────────────────────────────────────────────


def system_status(m, server: str) -> str:
    cpu_bar = "█" * int(m.cpu_percent / 10) + "░" * (10 - int(m.cpu_percent / 10))
    ram_bar = "█" * int(m.ram_percent / 10) + "░" * (10 - int(m.ram_percent / 10))
    disk_bar = "█" * int(m.disk_percent / 10) + "░" * (10 - int(m.disk_percent / 10))

    cpu_icon = "🔴" if m.cpu_percent >= 90 else "🟡" if m.cpu_percent >= 75 else "🟢"
    ram_icon = "🔴" if m.ram_percent >= 90 else "🟡" if m.ram_percent >= 80 else "🟢"
    disk_icon = "🔴" if m.disk_percent >= 90 else "🟡" if m.disk_percent >= 80 else "🟢"

    return (
        f"🖥️ <b>Server Status — {server}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{cpu_icon} <b>CPU</b>  [{cpu_bar}] {m.cpu_percent:.1f}% ({m.cpu_count} cores)\n"
        f"{ram_icon} <b>RAM</b>  [{ram_bar}] {m.ram_percent:.1f}% ({m.ram_used_gb}/{m.ram_total_gb} GB)\n"
        f"{disk_icon} <b>Disk</b> [{disk_bar}] {m.disk_percent:.1f}% ({m.disk_free_gb} GB free)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ <b>Load:</b> {m.load_avg[0]} · {m.load_avg[1]} · {m.load_avg[2]}\n"
        f"⏱️ <b>Uptime:</b> {m.uptime_human}\n"
        f"🕐 {_now_str()}"
    )
