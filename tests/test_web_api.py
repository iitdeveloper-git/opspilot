"""Tests for OpsPilot Web Command Center REST API endpoints."""

import pytest
from fastapi.testclient import TestClient

from opspilot.config import Settings
from opspilot.db.engine import init_db, set_db_path
from opspilot.web.app import create_web_app


@pytest.fixture(autouse=True)
async def test_db(tmp_path):
    db_file = tmp_path / "web_test.db"
    set_db_path(db_file)
    await init_db()
    yield
    set_db_path("data/opspilot.db")


@pytest.fixture
def auth_client():
    settings = Settings(web_enabled=True, admin_password="super-secret-admin-pass-123")
    app = create_web_app(settings)
    client = TestClient(app)

    # Login to establish authenticated cookie
    login_res = client.post("/api/auth/login", json={"password": "super-secret-admin-pass-123"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf_token
    return client


def test_unauthenticated_requests_return_401():
    """Unauthenticated requests to protected endpoints return 401 Unauthorized."""
    settings = Settings(web_enabled=True, admin_password="super-secret-admin-pass-123")
    app = create_web_app(settings)
    client = TestClient(app)

    res = client.get("/api/overview")
    assert res.status_code == 401

    res = client.get("/api/containers")
    assert res.status_code == 401

    res = client.get("/api/probes")
    assert res.status_code == 401

    res = client.get("/api/renewals")
    assert res.status_code == 401


def test_overview_endpoint(auth_client):
    """GET /api/overview returns metrics, counts, and CSRF token."""
    res = auth_client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()
    assert "metrics" in data
    assert "counts" in data
    assert "cpu_percent" in data["metrics"]
    assert "containers_total" in data["counts"]


def test_containers_endpoint(auth_client):
    """GET /api/containers returns list of containers with statuses."""
    res = auth_client.get("/api/containers")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_probes_crud_lifecycle(auth_client):
    """Test full CRUD lifecycle for HTTP probes."""
    # 1. Add probe
    add_payload = {
        "name": "Local Test API",
        "url": "http://127.0.0.1:8088/api/overview",
        "expected_status": 200,
        "timeout_seconds": 3,
    }
    create_res = auth_client.post("/api/probes", json=add_payload)
    assert create_res.status_code == 200
    probe_id = create_res.json()["id"]

    # 2. List probes
    list_res = auth_client.get("/api/probes")
    assert list_res.status_code == 200
    probes = list_res.json()
    assert any(p["id"] == probe_id for p in probes)

    # 3. Toggle probe
    toggle_res = auth_client.post(f"/api/probes/{probe_id}/toggle", json={"enabled": False})
    assert toggle_res.status_code == 200

    # 4. Delete probe
    del_res = auth_client.delete(f"/api/probes/{probe_id}")
    assert del_res.status_code == 200


def test_renewals_crud_lifecycle(auth_client):
    """Test full CRUD lifecycle for renewals and client billing."""
    # 1. Add renewal
    ren_payload = {
        "name": "Knowledge King Edu Hosting",
        "category": "client_billing",
        "due_date": "2026-11-01",
        "amount": 2500,
        "currency": "INR",
        "recurrence": "yearly",
        "notes": "Annual payment",
        "remind_days_before": 14,
    }
    create_res = auth_client.post("/api/renewals", json=ren_payload)
    assert create_res.status_code == 200
    ren_id = create_res.json()["id"]

    # 2. List renewals
    list_res = auth_client.get("/api/renewals")
    assert list_res.status_code == 200
    assert any(r["id"] == ren_id for r in list_res.json())

    # 3. Snooze renewal
    snooze_res = auth_client.post(f"/api/renewals/{ren_id}/snooze", json={"days": 10})
    assert snooze_res.status_code == 200

    # 4. Mark paid (advances yearly recurring date)
    pay_res = auth_client.post(f"/api/renewals/{ren_id}/pay", json={"paid_by": "Ravi"})
    assert pay_res.status_code == 200
    data = pay_res.json()
    assert data["next_renewal"] is not None
    assert data["next_renewal"]["due_date"] == "2027-11-01"

    # 5. Delete renewal
    del_res = auth_client.delete(f"/api/renewals/{ren_id}")
    assert del_res.status_code == 200

    # 6. Verify excluded from default active list
    list_active = auth_client.get("/api/renewals")
    assert list_active.status_code == 200
    assert not any(r["id"] == ren_id for r in list_active.json())


def test_incidents_endpoint(auth_client):
    """GET and DELETE /api/incidents."""
    res = auth_client.get("/api/incidents")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # Test delete 404 on nonexistent incident
    del_404 = auth_client.delete("/api/incidents/999999")
    assert del_404.status_code == 404


def test_renewal_edit_endpoint(auth_client):
    """PUT /api/renewals/{id} updates renewal fields in database."""
    # 1. Create a renewal
    ren_payload = {
        "name": "Initial Name",
        "category": "domain",
        "due_date": "2026-12-01",
        "amount": 1000,
        "currency": "INR",
        "recurrence": "none",
        "notes": "Original note",
        "remind_days_before": 7,
    }
    create_res = auth_client.post("/api/renewals", json=ren_payload)
    assert create_res.status_code == 200
    ren_id = create_res.json()["id"]

    # 2. Update the renewal via PUT
    update_payload = {
        "name": "Updated Client Name",
        "category": "client_billing",
        "due_date": "2026-12-15",
        "amount": 2500,
        "currency": "INR",
        "recurrence": "yearly",
        "notes": "Updated billing note",
        "remind_days_before": 10,
    }
    put_res = auth_client.put(f"/api/renewals/{ren_id}", json=update_payload)
    assert put_res.status_code == 200
    assert put_res.json()["success"] is True

    # 3. Verify in list
    list_res = auth_client.get("/api/renewals")
    assert list_res.status_code == 200
    updated = next(r for r in list_res.json() if r["id"] == ren_id)
    assert updated["name"] == "Updated Client Name"
    assert updated["category"] == "client_billing"
    assert updated["due_date"] == "2026-12-15"
    assert updated["amount"] == 2500


def test_settings_read_and_update_endpoint(auth_client):
    """GET and POST /api/settings reads and updates alert chat ID dynamically."""
    # 1. Read current settings
    read_res = auth_client.get("/api/settings")
    assert read_res.status_code == 200
    assert "alert_chat_id" in read_res.json()
    assert "server_name" in read_res.json()

    # 2. Update alert chat ID
    update_res = auth_client.post("/api/settings", json={"alert_chat_id": "-100999888777"})
    assert update_res.status_code == 200
    assert update_res.json()["alert_chat_id"] == "-100999888777"

    # 3. Verify persisted in GET
    verify_res = auth_client.get("/api/settings")
    assert verify_res.status_code == 200
    assert verify_res.json()["alert_chat_id"] == "-100999888777"


def test_docker_prune_endpoint(auth_client, monkeypatch):
    """POST /api/docker/prune triggers docker resource cleanup."""
    from opspilot.web import api

    async def mock_prune(self):
        return {"success": True, "reclaimed_mb": 142}

    monkeypatch.setattr(api.SafeOperationExecutor, "prune_docker", mock_prune)
    res = auth_client.post("/api/docker/prune")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["reclaimed_mb"] == 142


def test_ai_rca_endpoint(auth_client):
    """POST /api/ai/rca performs SRE Root Cause Analysis diagnosis."""
    payload = {
        "service_name": "growth-worker-prod",
        "container_status": "exited",
        "logs": "Fatal error: Out of memory. Killed process 124 (python). Exit code 137.",
    }
    res = auth_client.post("/api/ai/rca", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "diagnosis" in data
    assert "OOM" in data["diagnosis"] or "Memory" in data["diagnosis"]


def test_domains_api_crud_and_check(auth_client, monkeypatch):
    """Test full CRUD lifecycle and check endpoint for SSL domains."""
    from opspilot.db import domains
    from opspilot.monitor.ssl import SSLStatus

    def mock_check(domain: str, port: int = 443, timeout: int = 5):
        return SSLStatus(
            domain=domain,
            is_valid=True,
            days_remaining=88,
            expires_at="2026-04-01",
            issuer="Let's Encrypt Authority",
            error=None,
        )

    monkeypatch.setattr(domains, "check_domain_ssl", mock_check)

    # 1. Add domain
    add_payload = {
        "domain": "api.testdomain.com",
        "port": 443,
    }
    create_res = auth_client.post("/api/domains", json=add_payload)
    assert create_res.status_code == 200
    created = create_res.json()
    assert created["success"] is True
    domain_record = created["domain"]
    assert domain_record["domain"] == "api.testdomain.com"
    assert domain_record["is_valid"] is True
    domain_id = domain_record["id"]

    # 2. List domains
    list_res = auth_client.get("/api/domains")
    assert list_res.status_code == 200
    all_domains = list_res.json()
    assert any(d["id"] == domain_id for d in all_domains)

    # 3. Trigger live check
    check_res = auth_client.post(f"/api/domains/{domain_id}/check")
    assert check_res.status_code == 200
    assert check_res.json()["domain"]["domain"] == "api.testdomain.com"

    # 3b. Update domain governance (registrar & expiration date)
    update_res = auth_client.put(
        f"/api/domains/{domain_id}",
        json={
            "port": 8443,
            "registrar": "Namecheap Inc.",
            "domain_expires_at": "2027-08-15",
        },
    )
    assert update_res.status_code == 200
    updated_domain = update_res.json()["domain"]
    assert updated_domain["registrar"] == "Namecheap Inc."
    assert updated_domain["domain_expires_at"] == "2027-08-15"

    # 4. Overview includes domains_total count
    overview_res = auth_client.get("/api/overview")
    assert overview_res.status_code == 200
    assert overview_res.json()["counts"]["domains_total"] >= 1

    # 5. Delete domain
    del_res = auth_client.delete(f"/api/domains/{domain_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True


def test_ai_endpoints_lifecycle(auth_client, monkeypatch):
    """Test AI status, RCA diagnosis, and Copilot endpoints."""
    # 1. AI status
    status_res = auth_client.get("/api/ai/status")
    assert status_res.status_code == 200
    assert "provider" in status_res.json()

    # 2. Mock AIProvider.generate_response
    async def mock_generate_response(self, system_prompt: str, user_prompt: str) -> str:
        return "Root cause: Memory leak in worker process. Fix: restart container and increase heap."

    monkeypatch.setattr("opspilot.web.api.AIProvider.generate_response", mock_generate_response)

    # 3. Test RCA with mock key
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    rca_res = auth_client.post(
        "/api/ai/rca",
        json={"service_name": "redis", "container_status": "running", "logs": "OOM killed process"},
    )
    assert rca_res.status_code == 200
    assert rca_res.json()["success"] is True
    assert "Memory leak" in rca_res.json()["diagnosis"]

    # 4. Test Copilot query
    copilot_res = auth_client.post(
        "/api/ai/copilot",
        json={"query": "How is the system health?"},
    )
    assert copilot_res.status_code == 200
    assert copilot_res.json()["success"] is True

    # 5. Test Dynamic AI Settings Save
    save_res = auth_client.post(
        "/api/settings/ai",
        json={
            "provider": "gemini",
            "model": "gemini-1.5-pro",
            "api_key": "AIzaSyDynamicTestKey123456",
            "base_url": "",
            "enabled": True,
        },
    )
    assert save_res.status_code == 200
    assert save_res.json()["success"] is True
    assert save_res.json()["model"] == "gemini-1.5-pro"

    # 6. Verify GET /api/settings returns masked key
    get_res = auth_client.get("/api/settings")
    assert get_res.status_code == 200
    ai_info = get_res.json()["ai"]
    assert ai_info["provider"] == "gemini"
    assert ai_info["model"] == "gemini-1.5-pro"
    assert ai_info["has_key"] is True
    assert "AIza" in ai_info["masked_key"]
    assert "••••" in ai_info["masked_key"]

    # 7. Test AI Connection Endpoint
    test_res = auth_client.post(
        "/api/ai/test",
        json={
            "provider": "gemini",
            "model": "gemini-1.5-pro",
            "api_key": "",
            "base_url": "",
        },
    )
    assert test_res.status_code == 200
    assert test_res.json()["success"] is True
    assert "latency_ms" in test_res.json()

    # 8. Test AI Model Discovery Endpoint
    async def mock_list_models(provider: str, api_key: str = "", base_url: str = ""):
        return [
            {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "recommended": True},
            {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "recommended": False},
        ]

    monkeypatch.setattr("opspilot.web.api.AIProvider.list_available_models", mock_list_models)

    models_res = auth_client.post(
        "/api/ai/models",
        json={
            "provider": "gemini",
            "api_key": "",
            "base_url": "",
        },
    )
    assert models_res.status_code == 200
    assert models_res.json()["success"] is True
    assert "models" in models_res.json()
    assert len(models_res.json()["models"]) == 2
    assert models_res.json()["default_model"] == "gemini-1.5-flash"
