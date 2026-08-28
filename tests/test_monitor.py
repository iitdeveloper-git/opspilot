import pytest

from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.probes import probe_http_endpoint
from opspilot.monitor.ssl import check_domain_ssl
from opspilot.monitor.system import collect_system_metrics


def test_collect_system_metrics():
    metrics = collect_system_metrics()
    assert metrics.cpu_percent >= 0
    assert metrics.ram_total_gb > 0
    assert metrics.disk_total_gb > 0
    assert metrics.disk_free_gb >= 0
    assert len(metrics.load_avg) == 3


def test_check_domain_ssl_invalid():
    status = check_domain_ssl("nonexistent.invalid.local", timeout=1)
    assert status.is_valid is False
    assert status.days_remaining == 0


def test_collect_docker_statuses():
    # Should safely return a list even without running docker
    statuses = collect_docker_statuses()
    assert isinstance(statuses, list)


@pytest.mark.asyncio
async def test_probe_http_endpoint_invalid():
    status = await probe_http_endpoint("Test Endpoint", "http://127.0.0.1:59999/health", timeout=1.0)
    assert status.is_healthy is False
    assert status.error is not None
