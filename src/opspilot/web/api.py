"""REST API endpoints for OpsPilot 2.0 Web Command Center."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from opspilot.core.audit import AuditLogger
from opspilot.core.executor import SafeOperationExecutor
from opspilot.core.ignored import parse_duration
from opspilot.db import endpoints as ep_db
from opspilot.db import incidents as inc_db
from opspilot.db import renewals as ren_db
from opspilot.db import snooze as snooze_db
from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.probes import probe_http_endpoint
from opspilot.monitor.system import collect_system_metrics
from opspilot.web.auth import get_current_session, verify_csrf

router = APIRouter(prefix="/api", tags=["API"])
audit_logger = AuditLogger()
executor = SafeOperationExecutor()


# ── Overview ──────────────────────────────────────────────────────────────────


@router.get("/overview")
async def get_overview(
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Retrieve system resource gauges and high-level fleet status."""
    metrics = await asyncio.to_thread(collect_system_metrics)
    containers = await asyncio.to_thread(collect_docker_statuses)
    endpoints = await ep_db.list_endpoints(enabled_only=False)
    renewals = await ren_db.list_renewals(status="pending")
    open_incidents = await inc_db.list_open_incidents(limit=5)

    c_running = sum(1 for c in containers if c.status == "running" and c.health != "unhealthy")
    c_unhealthy = sum(1 for c in containers if c.health == "unhealthy")
    c_exited = sum(1 for c in containers if c.status == "exited")

    return {
        "metrics": {
            "cpu_percent": metrics.cpu_percent,
            "ram_percent": metrics.ram_percent,
            "ram_used_gb": metrics.ram_used_gb,
            "ram_total_gb": metrics.ram_total_gb,
            "disk_percent": metrics.disk_percent,
            "disk_free_gb": metrics.disk_free_gb,
            "load_avg": metrics.load_avg,
            "uptime_human": metrics.uptime_human,
        },
        "counts": {
            "containers_total": len(containers),
            "containers_running": c_running,
            "containers_unhealthy": c_unhealthy,
            "containers_exited": c_exited,
            "endpoints_total": len(endpoints),
            "endpoints_enabled": sum(1 for e in endpoints if e["enabled"] == 1),
            "renewals_pending": len(renewals),
            "incidents_open": len(open_incidents),
        },
        "recent_incidents": open_incidents,
        "csrf_token": session.get("csrf", ""),
    }


# ── Containers Fleet ──────────────────────────────────────────────────────────


class ContainerSnoozeRequest(BaseModel):
    duration: str = Field(default="1h", description="e.g. 1h, 6h, 24h, forever")


@router.get("/containers")
async def list_containers(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[dict[str, Any]]:
    """List all Docker containers with statuses and snooze metadata."""
    containers = await asyncio.to_thread(collect_docker_statuses)
    snoozed_set = {s["container_name"] for s in await snooze_db.list_snoozed()}

    results = []
    for c in containers:
        results.append(
            {
                "name": c.name,
                "status": c.status,
                "health": c.health,
                "image": c.image,
                "id": c.id,
                "created": c.created,
                "is_snoozed": c.name in snoozed_set,
            }
        )
    return results


@router.post("/containers/{name}/restart", dependencies=[Depends(verify_csrf)])
async def restart_container(
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Safely restart a Docker container via SafeOperationExecutor."""
    res = await executor.restart_container(name)
    status_str = "SUCCESS" if res.get("success") else "FAILURE"
    audit_logger.record_action(
        user_id="web-admin",
        action="restart_container",
        target=name,
        status=status_str,
        details=res,
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "Restart failed"))
    return res


@router.get("/containers/{name}/logs")
async def get_container_logs(
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    tail: int = Query(default=50, ge=10, le=200),
) -> dict[str, Any]:
    """Retrieve tail logs for a Docker container safely via SafeOperationExecutor."""
    logs = await executor.get_container_logs(name, tail=tail)
    is_err = logs.startswith("Error fetching logs:")
    audit_logger.record_action(
        user_id="web-admin",
        action="view_logs",
        target=name,
        status="FAILURE" if is_err else "SUCCESS",
    )
    return {"name": name, "tail": tail, "logs": logs}


@router.post("/containers/{name}/snooze", dependencies=[Depends(verify_csrf)])
async def snooze_container_endpoint(
    name: str,
    req: ContainerSnoozeRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Snooze alerts for a container for the given duration."""
    td = parse_duration(req.duration)
    exp_dt = datetime.now(UTC) + td if td else None
    await snooze_db.snooze_container(name, exp_dt)
    audit_logger.record_action(
        user_id="web-admin",
        action="snooze_container",
        target=name,
        status="SUCCESS",
        details={"duration": req.duration},
    )
    return {"success": True, "message": f"Container {name} snoozed ({req.duration})."}


@router.post("/containers/{name}/unsnooze", dependencies=[Depends(verify_csrf)])
async def unsnooze_container_endpoint(
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Clear snooze for a container."""
    await snooze_db.unsnooze_container(name)
    audit_logger.record_action(
        user_id="web-admin",
        action="unsnooze_container",
        target=name,
        status="SUCCESS",
    )
    return {"success": True, "message": f"Container {name} unsnoozed."}


# ── HTTP Probes ───────────────────────────────────────────────────────────────


class ProbeCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    url: str = Field(min_length=5, max_length=500)
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_seconds: int = Field(default=5, ge=1, le=30)


class ProbeToggleRequest(BaseModel):
    enabled: bool


@router.get("/probes")
async def list_probes(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[dict[str, Any]]:
    """List all registered HTTP probes and perform quick health checks."""
    endpoints = await ep_db.list_endpoints(enabled_only=False)
    if not endpoints:
        return []

    # Concurrently probe endpoints with short timeout for quick response
    tasks = [probe_http_endpoint(ep["name"], ep["url"], ep["expected_status"], timeout=4.0) for ep in endpoints]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for ep, res in zip(endpoints, results, strict=True):
        item = dict(ep)
        if isinstance(res, BaseException):
            item["is_healthy"] = False
            item["status_code"] = None
            item["latency_ms"] = 0
            item["error"] = str(res)
        else:
            item["is_healthy"] = res.is_healthy
            item["status_code"] = res.status_code
            item["latency_ms"] = res.latency_ms
            item["error"] = res.error
        enriched.append(item)
    return enriched


@router.post("/probes", dependencies=[Depends(verify_csrf)])
async def create_probe(
    req: ProbeCreateRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Add a new HTTP probe to the database."""
    try:
        new_id = await ep_db.add_endpoint(
            name=req.name.strip(),
            url=req.url.strip(),
            expected_status=req.expected_status,
            timeout_seconds=req.timeout_seconds,
        )
        audit_logger.record_action(
            user_id="web-admin",
            action="add_probe",
            target=req.name,
            status="SUCCESS",
            details={"url": req.url},
        )
        return {"success": True, "id": new_id, "message": f"Probe '{req.name}' created."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to add probe: {e}") from e


@router.post("/probes/{probe_id}/toggle", dependencies=[Depends(verify_csrf)])
async def toggle_probe(
    probe_id: int,
    req: ProbeToggleRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Enable or disable an HTTP probe."""
    await ep_db.toggle_endpoint(probe_id, req.enabled)
    audit_logger.record_action(
        user_id="web-admin",
        action="toggle_probe",
        target=str(probe_id),
        status="SUCCESS",
        details={"enabled": req.enabled},
    )
    return {"success": True, "message": f"Probe #{probe_id} toggled to enabled={req.enabled}."}


@router.delete("/probes/{probe_id}", dependencies=[Depends(verify_csrf)])
async def delete_probe(
    probe_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Soft-delete an HTTP probe (sets enabled=0)."""
    await ep_db.remove_endpoint(probe_id)
    audit_logger.record_action(
        user_id="web-admin",
        action="remove_probe",
        target=str(probe_id),
        status="SUCCESS",
    )
    return {"success": True, "message": f"Probe #{probe_id} removed."}


# ── Renewals & Billing ────────────────────────────────────────────────────────


class RenewalUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="other", max_length=50)
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount: float | None = None
    currency: str = Field(default="INR", max_length=10)
    notes: str = Field(default="", max_length=500)
    recurrence: str = Field(default="none", max_length=20)
    remind_days_before: int = Field(default=7, ge=1, le=90)


class SettingsUpdateRequest(BaseModel):
    alert_chat_id: str = Field(min_length=1, max_length=100)


class RenewalCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    category: str = Field(default="other")
    due_date: str = Field(description="ISO 8601 YYYY-MM-DD")
    amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR")
    notes: str = Field(default="")
    recurrence: str = Field(default="none")
    remind_days_before: int = Field(default=7, ge=1, le=90)


class RenewalSnoozeRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=60)


@router.get("/renewals")
async def list_renewals_endpoint(
    session: Annotated[dict, Depends(get_current_session)],
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[dict[str, Any]]:
    """List registered renewals and client billing reminders."""
    return await ren_db.list_renewals(status=status_filter)


@router.post("/renewals", dependencies=[Depends(verify_csrf)])
async def create_renewal(
    req: RenewalCreateRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Create a new renewal or client payment reminder."""
    # Basic date validation
    try:
        datetime.strptime(req.due_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid due_date. Format must be YYYY-MM-DD.") from None

    new_id = await ren_db.add_renewal(
        name=req.name.strip(),
        category=req.category,
        due_date=req.due_date,
        amount=req.amount,
        currency=req.currency,
        notes=req.notes,
        recurrence=req.recurrence,
        remind_days_before=req.remind_days_before,
    )
    audit_logger.record_action(
        user_id="web-admin",
        action="add_renewal",
        target=req.name,
        status="SUCCESS",
        details={"due_date": req.due_date, "amount": req.amount},
    )
    return {"success": True, "id": new_id, "message": f"Renewal '{req.name}' registered."}


@router.post("/renewals/{renewal_id}/pay", dependencies=[Depends(verify_csrf)])
async def pay_renewal(
    renewal_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Mark renewal as paid and advance recurring due date if applicable."""
    next_r = await ren_db.mark_paid(renewal_id)
    audit_logger.record_action(
        user_id="web-admin",
        action="mark_paid",
        target=str(renewal_id),
        status="SUCCESS",
        details={"has_next": bool(next_r)},
    )
    return {
        "success": True,
        "message": f"Renewal #{renewal_id} marked as paid.",
        "next_renewal": next_r,
    }


@router.post("/renewals/{renewal_id}/snooze", dependencies=[Depends(verify_csrf)])
async def snooze_renewal_endpoint(
    renewal_id: int,
    req: RenewalSnoozeRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Snooze renewal reminders for N days."""
    until_dt = datetime.now(UTC) + timedelta(days=req.days)
    await ren_db.snooze_renewal(renewal_id, until_dt)
    audit_logger.record_action(
        user_id="web-admin",
        action="snooze_renewal",
        target=str(renewal_id),
        status="SUCCESS",
        details={"days": req.days},
    )
    return {"success": True, "message": f"Renewal #{renewal_id} snoozed for {req.days} days."}


@router.delete("/renewals/{renewal_id}", dependencies=[Depends(verify_csrf)])
async def delete_renewal_endpoint(
    renewal_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Cancel / soft-delete a renewal."""
    await ren_db.delete_renewal(renewal_id)
    audit_logger.record_action(
        user_id="web-admin",
        action="delete_renewal",
        target=str(renewal_id),
        status="SUCCESS",
    )
    return {"success": True, "message": f"Renewal #{renewal_id} deleted."}


# ── Incidents ─────────────────────────────────────────────────────────────────


@router.get("/incidents")
async def list_incidents_endpoint(
    session: Annotated[dict, Depends(get_current_session)],
    limit: int = Query(default=30, ge=5, le=100),
) -> list[dict[str, Any]]:
    """List recent system incidents."""
    return await inc_db.list_incidents(limit=limit)


@router.post("/incidents/{incident_id}/resolve", dependencies=[Depends(verify_csrf)])
async def resolve_incident_endpoint(
    incident_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Manually resolve an incident."""
    await inc_db.resolve_incident_by_id(incident_id)
    audit_logger.record_action(
        user_id="web-admin",
        action="resolve_incident",
        target=str(incident_id),
        status="SUCCESS",
    )
    return {"success": True, "message": f"Incident #{incident_id} marked as resolved."}


@router.put("/renewals/{renewal_id}", dependencies=[Depends(verify_csrf)])
async def update_renewal_endpoint(
    renewal_id: int,
    req: RenewalUpdateRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Update an existing client billing or renewal reminder."""
    existing = await ren_db.get_renewal(renewal_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Renewal #{renewal_id} not found.")

    await ren_db.update_renewal(
        renewal_id=renewal_id,
        name=req.name.strip(),
        category=req.category.strip(),
        due_date=req.due_date.strip(),
        amount=req.amount,
        currency=req.currency.strip().upper(),
        notes=req.notes.strip(),
        recurrence=req.recurrence.strip(),
        remind_days_before=req.remind_days_before,
    )
    audit_logger.record_action(
        user_id="web-admin",
        action="update_renewal",
        target=f"#{renewal_id} {req.name}",
        status="SUCCESS",
    )
    return {"success": True, "message": f"Renewal #{renewal_id} updated."}


@router.get("/settings")
async def get_settings_endpoint(
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Get runtime server settings."""
    from opspilot.db.store import get_setting

    settings = request.app.state.settings
    current_chat_id = await get_setting("alert_chat_id", settings.telegram_alert_chat_id)
    return {
        "alert_chat_id": current_chat_id,
        "server_name": settings.server_name,
        "environment": settings.environment,
    }


@router.post("/settings", dependencies=[Depends(verify_csrf)])
async def update_settings_endpoint(
    req: SettingsUpdateRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Update runtime settings (e.g. alert chat ID) immediately without restart."""
    from opspilot.db.store import set_setting

    new_chat_id = req.alert_chat_id.strip()
    await set_setting("alert_chat_id", new_chat_id)

    channel = getattr(request.app.state, "channel", None)
    if channel and hasattr(channel, "update_chat_id"):
        channel.update_chat_id(new_chat_id)

    audit_logger.record_action(
        user_id="web-admin",
        action="update_alert_chat_id",
        target=new_chat_id,
        status="SUCCESS",
    )
    return {"success": True, "message": "Alert chat ID updated successfully.", "alert_chat_id": new_chat_id}


# ── Alert Routing Rules ───────────────────────────────────────────────────────


class AlertRouteCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    chat_id: str = Field(..., min_length=1, max_length=64)
    categories: list[str] = Field(default_factory=lambda: ["all"])
    events: list[str] = Field(default_factory=list)
    enabled: bool = True


class AlertRouteUpdateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    chat_id: str = Field(..., min_length=1, max_length=64)
    categories: list[str] = Field(default_factory=lambda: ["all"])
    events: list[str] = Field(default_factory=list)
    enabled: bool = True


class AlertRouteTestRequest(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=64)


@router.get("/alert-routes")
async def list_alert_routes_endpoint(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[dict[str, Any]]:
    """List all configured alert routing rules."""
    from opspilot.db import alert_routes as alert_routes_db

    return await alert_routes_db.list_routes()


@router.post("/alert-routes", status_code=201, dependencies=[Depends(verify_csrf)])
async def create_alert_route_endpoint(
    req: AlertRouteCreateRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Create a new alert routing rule."""
    from opspilot.db import alert_routes as alert_routes_db

    route_id = await alert_routes_db.create_route(
        label=req.label,
        chat_id=req.chat_id,
        categories=req.categories,
        events=req.events,
        enabled=req.enabled,
    )
    route = await alert_routes_db.get_route(route_id)
    audit_logger.record_action(
        user_id="web-admin",
        action="create_alert_route",
        target=f"{req.label} ({req.chat_id})",
        status="SUCCESS",
    )
    return {"success": True, "route": route}


@router.put("/alert-routes/{route_id}", dependencies=[Depends(verify_csrf)])
async def update_alert_route_endpoint(
    route_id: int,
    req: AlertRouteUpdateRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Update an existing alert routing rule."""
    from opspilot.db import alert_routes as alert_routes_db

    success = await alert_routes_db.update_route(
        route_id=route_id,
        label=req.label,
        chat_id=req.chat_id,
        categories=req.categories,
        events=req.events,
        enabled=req.enabled,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Alert route not found")
    route = await alert_routes_db.get_route(route_id)
    audit_logger.record_action(
        user_id="web-admin",
        action="update_alert_route",
        target=f"#{route_id} {req.label}",
        status="SUCCESS",
    )
    return {"success": True, "route": route}


@router.delete("/alert-routes/{route_id}", dependencies=[Depends(verify_csrf)])
async def delete_alert_route_endpoint(
    route_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Delete an alert routing rule."""
    from opspilot.db import alert_routes as alert_routes_db

    success = await alert_routes_db.delete_route(route_id)
    if not success:
        raise HTTPException(status_code=404, detail="Alert route not found")
    audit_logger.record_action(
        user_id="web-admin",
        action="delete_alert_route",
        target=f"#{route_id}",
        status="SUCCESS",
    )
    return {"success": True, "message": f"Alert route #{route_id} deleted."}


@router.post("/alert-routes/test", dependencies=[Depends(verify_csrf)])
async def test_alert_route_endpoint(
    req: AlertRouteTestRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """Send an immediate verification test message to the specified Telegram Chat ID."""
    channel = getattr(request.app.state, "channel", None)
    if not channel or not hasattr(channel, "send_to_chat"):
        raise HTTPException(status_code=500, detail="Telegram channel is not initialized")
    success = await channel.send_to_chat(
        req.chat_id,
        "🧪 <b>OpsPilot 2.0 Test Alert</b>\n"
        "Your Telegram routing configuration is verified and receiving alerts! ✅",
    )
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to deliver message to chat '{req.chat_id}'. Check chat ID and bot permissions.",
        )
    return {"success": True, "message": f"Test message delivered to chat '{req.chat_id}' successfully!"}
