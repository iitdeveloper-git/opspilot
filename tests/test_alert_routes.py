"""Tests for Multi-Channel Alert Routing and Event Filtering."""

import pytest
from httpx import ASGITransport, AsyncClient

from opspilot.db import alert_routes as alert_routes_db
from opspilot.db.engine import init_db, set_db_path
from opspilot.web.app import create_web_app


class MockTelegramChannel:
    def __init__(self, chat_id: str = "-100123") -> None:
        self._chat_id = chat_id
        self.sent_messages: list[tuple[str, str]] = []

    def update_chat_id(self, chat_id: str) -> None:
        self._chat_id = chat_id

    async def send_to_chat(self, chat_id: str, text: str, keyboard=None) -> bool:
        self.sent_messages.append((chat_id, text))
        return True


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path):
    db_file = tmp_path / "test_routes.db"
    set_db_path(db_file)
    await init_db()


@pytest.mark.asyncio
async def test_alert_routes_crud():
    # 1. Create
    route_id = await alert_routes_db.create_route(
        label="Foodio Team",
        chat_id="-100999888",
        categories=["probes", "billing"],
        events=["foodio"],
        enabled=True,
    )
    assert route_id > 0

    # 2. Get & List
    route = await alert_routes_db.get_route(route_id)
    assert route is not None
    assert route["label"] == "Foodio Team"
    assert route["categories"] == ["probes", "billing"]
    assert route["events"] == ["foodio"]
    assert route["enabled"] is True

    routes = await alert_routes_db.list_routes()
    assert len(routes) == 1

    # 3. Update
    updated = await alert_routes_db.update_route(
        route_id=route_id,
        label="Foodio DevOps & Billing",
        chat_id="-100999888",
        categories=["probes", "billing", "containers"],
        events=["foodio", "foodio-backend"],
        enabled=True,
    )
    assert updated is True

    route = await alert_routes_db.get_route(route_id)
    assert route is not None
    assert "containers" in route["categories"]
    assert "foodio-backend" in route["events"]

    # 4. Delete
    deleted = await alert_routes_db.delete_route(route_id)
    assert deleted is True
    assert await alert_routes_db.get_route(route_id) is None


@pytest.mark.asyncio
async def test_alert_routes_matching_logic():
    # Route 1: All probe alerts
    await alert_routes_db.create_route(
        label="DevOps Probes",
        chat_id="-100_PROBES",
        categories=["probes"],
        events=[],  # all probes
        enabled=True,
    )

    # Route 2: Specific Foodio alerts (any category, target=foodio)
    await alert_routes_db.create_route(
        label="Foodio Only",
        chat_id="-100_FOODIO",
        categories=["all"],
        events=["foodio"],
        enabled=True,
    )

    # Route 3: Billing only
    await alert_routes_db.create_route(
        label="Finance",
        chat_id="-100_FINANCE",
        categories=["billing"],
        events=[],
        enabled=True,
    )

    # Match Probe for Growixa: should go to -100_PROBES only
    matched = await alert_routes_db.get_matching_chat_ids(
        category="probes",
        target="growixa.iitdeveloper.com",
        event="probe_failed",
        default_chat_id="-100_DEFAULT",
    )
    assert "-100_PROBES" in matched
    assert "-100_FOODIO" not in matched
    assert "-100_FINANCE" not in matched

    # Match Probe for Foodio: should go to BOTH -100_PROBES and -100_FOODIO!
    matched_foodio = await alert_routes_db.get_matching_chat_ids(
        category="probes",
        target="https://foodio.iitdeveloper.com",
        event="probe_failed",
        default_chat_id="-100_DEFAULT",
    )
    assert "-100_PROBES" in matched_foodio
    assert "-100_FOODIO" in matched_foodio
    assert "-100_FINANCE" not in matched_foodio

    # Match Billing: should go to -100_FINANCE
    matched_billing = await alert_routes_db.get_matching_chat_ids(
        category="billing",
        target="Pulsar IP Renewal",
        event="renewal_due",
        default_chat_id="-100_DEFAULT",
    )
    assert "-100_FINANCE" in matched_billing
    assert "-100_PROBES" not in matched_billing

    # Match Unmatched category (e.g. system disk): fallback to default
    matched_disk = await alert_routes_db.get_matching_chat_ids(
        category="system",
        target="disk",
        event="disk_critical",
        default_chat_id="-100_DEFAULT",
    )
    assert matched_disk == ["-100_DEFAULT"]


@pytest.mark.asyncio
async def test_alert_routes_api_endpoints():
    from opspilot.config import Settings

    settings = Settings(web_enabled=True, admin_password="test-password")
    mock_channel = MockTelegramChannel()
    app = create_web_app(settings, channel=mock_channel)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Login to get session
        login_resp = await client.post(
            "/api/auth/login",
            json={"password": "test-password"},
        )
        assert login_resp.status_code == 200
        csrf = login_resp.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}

        # 1. Create Route
        create_resp = await client.post(
            "/api/alert-routes",
            json={
                "label": "Test Route",
                "chat_id": "-100777",
                "categories": ["probes", "containers"],
                "events": ["foodio"],
                "enabled": True,
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        route_id = create_resp.json()["route"]["id"]

        # 2. List Routes
        list_resp = await client.get("/api/alert-routes")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # 3. Update Route
        put_resp = await client.put(
            f"/api/alert-routes/{route_id}",
            json={
                "label": "Updated Route",
                "chat_id": "-100777",
                "categories": ["all"],
                "events": ["foodio", "caddy"],
                "enabled": True,
            },
            headers=headers,
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["route"]["label"] == "Updated Route"

        # 4. Test Alert Route
        test_resp = await client.post(
            "/api/alert-routes/test",
            json={"chat_id": "-100777"},
            headers=headers,
        )
        assert test_resp.status_code == 200
        assert "delivered" in test_resp.json()["message"]
        assert len(mock_channel.sent_messages) == 1
        assert mock_channel.sent_messages[0][0] == "-100777"

        # 5. Delete Route
        del_resp = await client.delete(
            f"/api/alert-routes/{route_id}",
            headers=headers,
        )
        assert del_resp.status_code == 200
