import logging

logger = logging.getLogger("opspilot.security")


class SecurityError(Exception):
    """Raised when an unauthorized action is attempted."""

    pass


class AccessController:
    """Controls access to OpsPilot operations.

    Security model:
    - Production (default): DENY ALL unless user_id is in the allowlist.
      If the allowlist is empty, startup should warn loudly and deny everyone.
    - Development: set OPSPILOT_AUTH_MODE=development to allow all users
      (unsafe — never use in production).
    """

    def __init__(self, allowed_user_ids: set[int], auth_mode: str = "production"):
        self.allowed_user_ids = allowed_user_ids
        self.auth_mode = auth_mode.lower().strip()

        if self.auth_mode == "development":
            logger.warning(
                "⚠️  OpsPilot is running in DEVELOPMENT auth mode. "
                "ALL Telegram users can control this bot. "
                "Never use OPSPILOT_AUTH_MODE=development in production."
            )
        elif not self.allowed_user_ids:
            # Fail closed: no allowlist in production = deny everyone and warn loudly
            logger.error(
                "🚨 SECURITY: TELEGRAM_ALLOWED_USER_IDS is not configured. "
                "OpsPilot will DENY ALL incoming requests. "
                "Set TELEGRAM_ALLOWED_USER_IDS in your .env file, "
                "or set OPSPILOT_AUTH_MODE=development for local testing only."
            )

    def is_authorized(self, user_id: int) -> bool:
        """Return True only if the user is explicitly allowed, or dev mode is active."""
        if self.auth_mode == "development":
            return True  # Explicit opt-in to open access
        if not self.allowed_user_ids:
            return False  # Fail closed: no allowlist → deny all
        return user_id in self.allowed_user_ids

    def require_auth(self, user_id: int) -> None:
        """Raise SecurityError if the user is not authorized."""
        if not self.is_authorized(user_id):
            logger.warning(f"Unauthorized access attempt from user_id={user_id}")
            raise SecurityError(f"User {user_id} is not authorized to control OpsPilot.")
