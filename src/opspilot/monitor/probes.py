import logging

import httpx
from pydantic import BaseModel

logger = logging.getLogger("opspilot.monitor.probes")

DEFAULT_PROBE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OpsPilot/2.0; +https://github.com/iitdeveloper-git/opspilot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ProbeResult(BaseModel):
    name: str
    url: str
    status_code: int | None
    is_healthy: bool
    latency_ms: float
    error: str | None = None


async def probe_http_endpoint(
    name: str,
    url: str,
    expected_status: int = 200,
    timeout: float = 8.0,
) -> ProbeResult:
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=False,
            follow_redirects=True,
            headers=DEFAULT_PROBE_HEADERS,
        ) as client:
            resp = await client.get(url)
            latency = resp.elapsed.total_seconds() * 1000
            is_ok = resp.status_code == expected_status
            return ProbeResult(
                name=name,
                url=url,
                status_code=resp.status_code,
                is_healthy=is_ok,
                latency_ms=round(latency, 2),
            )
    except Exception as e:
        return ProbeResult(
            name=name,
            url=url,
            status_code=None,
            is_healthy=False,
            latency_ms=0.0,
            error=str(e),
        )
