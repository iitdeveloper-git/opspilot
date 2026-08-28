import pytest
from opspilot.core.security import AccessController, SecurityError


def test_access_controller_authorized():
    ac = AccessController(allowed_user_ids={111, 222})
    assert ac.is_authorized(111) is True
    assert ac.is_authorized(333) is False


def test_access_controller_raises_unauthorized():
    ac = AccessController(allowed_user_ids={111})
    with pytest.raises(SecurityError):
        ac.require_auth(999)
