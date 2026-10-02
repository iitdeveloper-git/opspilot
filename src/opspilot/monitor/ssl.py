import logging
import socket
import ssl
from datetime import UTC, datetime

from typing import Any

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


def extract_apex_domain(domain: str) -> str:
    """Extract root apex domain from subdomains (e.g. auth.iitdeveloper.com -> iitdeveloper.com)."""
    d = domain.lower().strip()
    if ":" in d:
        d = d.split(":")[0]
    multi_tlds = (
        ".co.uk", ".org.uk", ".gov.uk", ".me.uk",
        ".co.in", ".net.in", ".org.in", ".gov.in",
        ".com.au", ".net.au", ".org.au",
        ".co.nz", ".co.za", ".co.jp",
    )
    for mtld in multi_tlds:
        if d.endswith(mtld):
            parts = d.split(".")
            if len(parts) >= 3:
                return ".".join(parts[-3:])
            return d
    parts = d.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return d


def check_domain_rdap(domain: str, timeout: int = 5) -> dict[str, Any]:
    """Query ICANN RDAP to discover domain registrar and registration renewal expiry date."""
    apex = extract_apex_domain(domain)
    url = f"https://rdap.org/domain/{apex}"
    result = {
        "registrar": "",
        "domain_expires_at": "",
        "domain_days_remaining": 0,
        "registration_date": "",
    }
    try:
        import httpx

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "OpsPilot/2.0 Domain-Governance"})
            if resp.status_code == 200:
                data = resp.json()
                events = data.get("events", [])
                exp_raw = next((e["eventDate"] for e in events if e.get("eventAction") == "expiration"), None)
                reg_raw = next((e["eventDate"] for e in events if e.get("eventAction") == "registration"), None)

                if exp_raw:
                    try:
                        exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                        now = datetime.now(UTC)
                        days = (exp_dt - now).days
                        result["domain_expires_at"] = exp_dt.strftime("%Y-%m-%d")
                        result["domain_days_remaining"] = max(0, days)
                    except Exception:
                        result["domain_expires_at"] = exp_raw[:10]

                if reg_raw:
                    try:
                        reg_dt = datetime.fromisoformat(reg_raw.replace("Z", "+00:00"))
                        result["registration_date"] = reg_dt.strftime("%Y-%m-%d")
                    except Exception:
                        result["registration_date"] = reg_raw[:10]

                for entity in data.get("entities", []):
                    if "registrar" in entity.get("roles", []):
                        vcards = entity.get("vcardArray", [[]])
                        if len(vcards) > 1 and isinstance(vcards[1], list):
                            for entry in vcards[1]:
                                if len(entry) > 3 and entry[0] == "fn":
                                    result["registrar"] = str(entry[3]).strip()
                                    break
                    if result["registrar"]:
                        break
    except Exception as e:
        logger.debug(f"RDAP lookup failed for {domain} ({apex}): {e}")
    return result
