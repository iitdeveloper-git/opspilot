import pytest

from opspilot.core.security import AccessController, SecurityError


def test_access_controller_authorized_user():
    ac = AccessController(allowed_user_ids={111, 222}, auth_mode="production")
    assert ac.is_authorized(111) is True
    assert ac.is_authorized(222) is True
    assert ac.is_authorized(333) is False


def test_access_controller_empty_allowlist_fails_closed_in_prod():
    """Security requirement: empty allowlist in production MUST deny all."""
    ac = AccessController(allowed_user_ids=set(), auth_mode="production")
    assert ac.is_authorized(111) is False
    assert ac.is_authorized(222) is False
    assert ac.is_authorized(0) is False


def test_access_controller_development_mode_allows_all():
    """Development mode must be explicitly opt-in."""
    ac = AccessController(allowed_user_ids=set(), auth_mode="development")
    assert ac.is_authorized(111) is True
    assert ac.is_authorized(999999) is True


def test_access_controller_require_auth_raises_unauthorized():
    ac = AccessController(allowed_user_ids={111}, auth_mode="production")
    ac.require_auth(111)  # should not raise
    with pytest.raises(SecurityError) as exc_info:
        ac.require_auth(999)
    assert "not authorized" in str(exc_info.value)
