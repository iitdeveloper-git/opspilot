import logging
from typing import Callable, Any
from functools import wraps

logger = logging.getLogger("opspilot.security")


class SecurityError(Exception):
    """Raised when an unauthorized action is attempted."""
    pass


class AccessController:
    def __init__(self, allowed_user_ids: set[int]):
        self.allowed_user_ids = allowed_user_ids

    def is_authorized(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True  # If empty, allow in dev/local mode
        return user_id in self.allowed_user_ids

    def require_auth(self, user_id: int) -> None:
        if not self.is_authorized(user_id):
            logger.warning(f"Unauthorized access attempt from user_id={user_id}")
            raise SecurityError(f"User {user_id} is not authorized to control OpsPilot.")
