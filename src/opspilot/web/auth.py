"""FastAPI authentication dependencies and CSRF verification."""

from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

SESSION_COOKIE_NAME = "opspilot_session"


def get_client_ip(request: Request) -> str:
    """Extract client IP, verifying X-Forwarded-For is only trusted from local/private reverse proxies."""
    peer = request.client.host if request.client else "127.0.0.1"
    is_trusted = False
    try:
        addr = ipaddress.ip_address(peer)
        is_trusted = addr.is_loopback or addr.is_private
    except ValueError:
        is_trusted = False

    if is_trusted:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[0]
    return peer


def get_current_session(request: Request) -> dict:
    """
    FastAPI dependency validating the signed session cookie.
    Raises HTTP 401 Unauthorized if missing, tampered, or expired.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
        )

    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session manager not initialised.",
        )

    payload = session_mgr.verify_session(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
        )
    return payload


def verify_csrf(request: Request, session: Annotated[dict, Depends(get_current_session)]) -> None:
    """
    FastAPI dependency enforcing CSRF token validation on state-changing requests.
    Validates that X-CSRF-Token header strictly matches session's cryptographic CSRF token.
    Raises HTTP 403 Forbidden on mismatch or omission.
    """
    header_csrf = request.headers.get("X-CSRF-Token", "").strip()
    session_csrf = session.get("csrf", "")

    if not header_csrf or not session_csrf or not hmac.compare_digest(header_csrf, session_csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed. Missing or invalid X-CSRF-Token header.",
        )
