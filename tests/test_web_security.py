"""Tests for OpsPilot Web Command Center security, authentication, and CSRF protection."""

import pytest
from pydantic import ValidationError

from opspilot.config import Settings
from opspilot.web.security import (
    LoginRateLimiter,
    create_session_manager,
    verify_password,
)


def test_web_disabled_by_default():
    """Web Command Center must be disabled by default and bound strictly to localhost."""
    settings = Settings()
    assert settings.web_enabled is False
    assert settings.web_host == "127.0.0.1"
    assert settings.web_port == 8088


def test_web_enabled_requires_strong_admin_password():
    """Enabling web with empty or weak password must refuse to initialize."""
    with pytest.raises(ValidationError):
        Settings(web_enabled=True, admin_password="")

    with pytest.raises(ValidationError):
        Settings(web_enabled=True, admin_password="short")  # <12 chars

    # 12+ chars passes
    valid = Settings(web_enabled=True, admin_password="super-secret-admin-pass-123")
    assert valid.web_enabled is True
    assert valid.admin_password == "super-secret-admin-pass-123"


def test_constant_time_password_verification():
    """Constant-time password verification behaves correctly."""
    assert verify_password("CorrectHorseBatteryStaple", "CorrectHorseBatteryStaple") is True
    assert verify_password("WrongPassword", "CorrectHorseBatteryStaple") is False
    assert verify_password("", "CorrectHorseBatteryStaple") is False
    assert verify_password("CorrectHorseBatteryStaple", "") is False


def test_login_rate_limiting_and_lockout():
    """5 failed attempts locks out client IP for the window; reset clears."""
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=60)
    test_ip = "192.168.1.100"

    # Initial state: not locked
    locked, _ = limiter.is_locked(test_ip)
    assert locked is False

    # Record 4 failures: still not locked
    for _ in range(4):
        limiter.record_failure(test_ip)
    locked, _ = limiter.is_locked(test_ip)
    assert locked is False

    # 5th failure triggers lockout
    limiter.record_failure(test_ip)
    locked, remaining = limiter.is_locked(test_ip)
    assert locked is True
    assert remaining > 0

    # Reset upon success
    limiter.reset(test_ip)
    locked, _ = limiter.is_locked(test_ip)
    assert locked is False


def test_session_token_signing_and_tampering():
    """Tampered or invalid session tokens must be rejected."""
    mgr = create_session_manager("super-secret-admin-pass-123")
    token, csrf = mgr.create_session("admin")

    # Valid token unpacks correctly
    payload = mgr.verify_session(token)
    assert payload is not None
    assert payload["user"] == "admin"
    assert payload["csrf"] == csrf

    # Tampered token fails
    tampered = token[:-4] + "abcd"
    assert mgr.verify_session(tampered) is None


@pytest.mark.asyncio
async def test_csrf_rejection_on_post(tmp_path):
    """State-changing requests require a valid X-CSRF-Token header."""
    from fastapi.testclient import TestClient

    from opspilot.db.engine import init_db, set_db_path
    from opspilot.web.app import create_web_app

    set_db_path(tmp_path / "csrf_test.db")
    await init_db()

    settings = Settings(web_enabled=True, admin_password="super-secret-admin-pass-123")
    app = create_web_app(settings)
    client = TestClient(app)

    # 1. Login to get cookie and CSRF token
    login_res = client.post("/api/auth/login", json={"password": "super-secret-admin-pass-123"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    # 2. POST without X-CSRF-Token must return 403 Forbidden
    probe_payload = {"name": "Test", "url": "https://example.com", "expected_status": 200}
    blocked_res = client.post("/api/probes", json=probe_payload)
    assert blocked_res.status_code == 403
    assert "CSRF" in blocked_res.json()["detail"]

    # 3. POST with mismatched X-CSRF-Token must return 403 Forbidden
    bad_res = client.post("/api/probes", json=probe_payload, headers={"X-CSRF-Token": "invalid-token"})
    assert bad_res.status_code == 403

    # 4. POST with matching X-CSRF-Token passes authentication & CSRF check
    good_res = client.post("/api/probes", json=probe_payload, headers={"X-CSRF-Token": csrf_token})
    assert good_res.status_code == 200
