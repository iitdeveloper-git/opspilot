from opspilot.monitor.system import collect_system_metrics


def test_collect_system_metrics():
    metrics = collect_system_metrics()
    assert metrics.cpu_percent >= 0
    assert metrics.ram_total_gb > 0
    assert metrics.disk_total_gb > 0
    assert metrics.disk_free_gb >= 0
    assert len(metrics.load_avg) == 3
