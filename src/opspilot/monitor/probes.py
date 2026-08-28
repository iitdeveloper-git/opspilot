import httpx
import logging
from pydantic import BaseModel

logger = logging.getLogger("opspilot.monitor.probes")


class ProbeResult(BaseModel):
    name: str
    url: str
    status_code: int | None
    is_healthy: bool
    latency_ms: float
    error: str | None = None


async def probe_http_endpoint(name: str, url: str, expected_status: int = 200, timeout: float = 5.0) -> ProbeResult:
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
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
