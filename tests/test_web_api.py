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
    pay_res = auth_client.post(f"/api/renewals/{ren_id}/pay")
    assert pay_res.status_code == 200
    data = pay_res.json()
    assert data["next_renewal"] is not None
    assert data["next_renewal"]["due_date"] == "2027-11-01"

    # 5. Delete renewal
    del_res = auth_client.delete(f"/api/renewals/{ren_id}")
    assert del_res.status_code == 200


def test_incidents_endpoint(auth_client):
    """GET /api/incidents returns incident list."""
    res = auth_client.get("/api/incidents")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


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
