import logging
import socket
import ssl
from datetime import UTC, datetime

from pydantic import BaseModel

logger = logging.getLogger("opspilot.monitor.ssl")


class SSLStatus(BaseModel):
    domain: str
    is_valid: bool
    days_remaining: int
    expires_at: str
    issuer: str
    error: str | None = None


def check_domain_ssl(domain: str, port: int = 443, timeout: int = 5) -> SSLStatus:
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection((domain, port), timeout=timeout) as sock,
            context.wrap_socket(sock, server_hostname=domain) as ssock,
        ):
            cert = ssock.getpeercert()
            if not cert:
                return SSLStatus(
                    domain=domain,
                    is_valid=False,
                    days_remaining=0,
                    expires_at="",
                    issuer="",
                    error="No certificate received",
                )

            # Date format: May 28 12:00:00 2026 GMT
            not_after_str = cert["notAfter"]
            expires = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)  # type: ignore[arg-type]
            now = datetime.now(UTC)
            days_left = (expires - now).days

            issuer_info = dict(x[0] for x in cert.get("issuer", []))  # type: ignore[misc]
            issuer_name = issuer_info.get("organizationName", issuer_info.get("commonName", "Unknown"))

            return SSLStatus(
                domain=domain,
                is_valid=days_left > 0,
                days_remaining=days_left,
                expires_at=expires.strftime("%Y-%m-%d"),
                issuer=issuer_name,
            )
    except Exception as e:
        logger.warning(f"SSL check failed for {domain}: {e}")
        return SSLStatus(
            domain=domain,
            is_valid=False,
            days_remaining=0,
            expires_at="",
            issuer="",
            error=str(e),
        )
