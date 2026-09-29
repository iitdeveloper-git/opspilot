"""
Security engine for OpsPilot Web Command Center:
- Constant-time password verification (anti-timing attacks)
- In-memory rate limiting and brute-force lockout (anti-credential stuffing)
- Cryptographically signed session cookies with itsdangerous
- Cryptographic CSRF token generation and validation
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# ── 1. Constant-Time Password Verification ───────────────────────────────────


def verify_password(provided: str, actual: str) -> bool:
    """
    Compare provided password against actual password in constant time.
    Hashes both to SHA-256 first to normalize lengths, preventing timing leaks.
    """
    if not provided or not actual:
        return False
    provided_hash = hashlib.sha256(provided.encode("utf-8")).digest()
    actual_hash = hashlib.sha256(actual.encode("utf-8")).digest()
    return hmac.compare_digest(provided_hash, actual_hash)


# ── 2. Brute-Force Rate Limiting & Lockout ───────────────────────────────────


class LoginRateLimiter:
    """
    In-memory rate limiter tracking failed login attempts per client IP.
    Enforces maximum of max_attempts (default 5) within window_seconds (default 900s / 15 mins).
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        # ip -> list of failed timestamp floats
        self._failures: dict[str, list[float]] = defaultdict(list)

    def is_locked(self, ip: str) -> tuple[bool, int]:
        """
        Check if IP is currently locked out.
        Returns (is_locked, remaining_seconds).
        """
        now = time.time()
        # Filter attempts within current window
        cutoff = now - self.window_seconds
        attempts = [t for t in self._failures[ip] if t > cutoff]
        self._failures[ip] = attempts

        if len(attempts) >= self.max_attempts:
            oldest = attempts[0]
            remaining = int(self.window_seconds - (now - oldest))
            return True, max(1, remaining)
        return False, 0

    def record_failure(self, ip: str) -> None:
        """Record a failed login attempt for the given IP."""
        now = time.time()
        cutoff = now - self.window_seconds
        attempts = [t for t in self._failures[ip] if t > cutoff]
        attempts.append(now)
        self._failures[ip] = attempts

    def reset(self, ip: str) -> None:
        """Clear failed attempts upon successful authentication."""
        self._failures.pop(ip, None)


login_rate_limiter = LoginRateLimiter(max_attempts=5, window_seconds=900)


# ── 3. Signed Session Token Serializer ───────────────────────────────────────


class SessionManager:
    """Manages signed, tamper-proof session tokens for HTTP cookies."""

    def __init__(self, secret_key: str, max_age_seconds: int = 28800) -> None:
        # 28800s = 8 hours
        self.serializer = URLSafeTimedSerializer(secret_key, salt="opspilot-web-session")
        self.max_age_seconds = max_age_seconds

    def create_session(self, user: str = "admin") -> tuple[str, str]:
        """
        Generates a new session token and paired CSRF token.
        Returns (session_cookie_value, csrf_token).
        """
        csrf_token = secrets.token_urlsafe(32)
        payload = {
            "user": user,
            "csrf": csrf_token,
            "iat": int(time.time()),
        }
        token = self.serializer.dumps(payload)
        return token, csrf_token

    def verify_session(self, token: str) -> dict | None:
        """
        Verify signed session token. Returns payload dict or None if invalid/expired.
        """
        try:
            payload = self.serializer.loads(token, max_age=self.max_age_seconds)
            if isinstance(payload, dict) and "csrf" in payload:
                return payload
            return None
        except (BadSignature, SignatureExpired):
            return None


def create_session_manager(admin_password: str) -> SessionManager:
    """Derive session signing key from admin password and return SessionManager."""
    salt = b"opspilot-web-auth-secret-salt-v1"
    key = hashlib.pbkdf2_hmac("sha256", admin_password.encode("utf-8"), salt, 10000).hex()
    return SessionManager(secret_key=key)
