import time
from datetime import timedelta

import psutil
from pydantic import BaseModel


class SystemMetrics(BaseModel):
    cpu_percent: float
    cpu_count: int
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_percent: float
    uptime_human: str
    load_avg: list[float]


def collect_system_metrics() -> SystemMetrics:
    cpu_pct = psutil.cpu_percent(interval=0.5)
    cpu_cnt = psutil.cpu_count(logical=True) or 1

    vm = psutil.virtual_memory()
    ram_total = round(vm.total / (1024**3), 2)
    ram_used = round(vm.used / (1024**3), 2)
    ram_pct = vm.percent

    disk = psutil.disk_usage("/")
    disk_total = round(disk.total / (1024**3), 2)
    disk_used = round(disk.used / (1024**3), 2)
    disk_free = round(disk.free / (1024**3), 2)
    disk_pct = disk.percent

    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)
    uptime_str = str(timedelta(seconds=uptime_seconds))

    load_avg = [round(x, 2) for x in psutil.getloadavg()] if hasattr(psutil, "getloadavg") else [0.0, 0.0, 0.0]

    return SystemMetrics(
        cpu_percent=cpu_pct,
        cpu_count=cpu_cnt,
        ram_total_gb=ram_total,
        ram_used_gb=ram_used,
        ram_percent=ram_pct,
        disk_total_gb=disk_total,
        disk_used_gb=disk_used,
        disk_free_gb=disk_free,
        disk_percent=disk_pct,
        uptime_human=uptime_str,
        load_avg=load_avg,
    )
