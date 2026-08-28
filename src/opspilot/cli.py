import asyncio

import typer
from rich.console import Console
from rich.table import Table

from opspilot.main import run_daemon
from opspilot.monitor.docker import collect_docker_statuses
from opspilot.monitor.system import collect_system_metrics

app = typer.Typer(help="OpsPilot: AI-powered infrastructure command center")
console = Console()


@app.command()
def status():
    """Show current server metrics and health."""
    m = collect_system_metrics()
    table = Table(title=f"Server Status ({m.uptime_human} uptime)")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold green")

    table.add_row("CPU Usage", f"{m.cpu_percent}% ({m.cpu_count} cores)")
    table.add_row("Memory", f"{m.ram_percent}% ({m.ram_used_gb}/{m.ram_total_gb} GB)")
    table.add_row("Disk (/)", f"{m.disk_percent}% ({m.disk_used_gb}/{m.disk_total_gb} GB, {m.disk_free_gb} GB free)")
    table.add_row("Load Avg", f"{m.load_avg}")
    console.print(table)


@app.command()
def ps():
    """List Docker containers and health."""
    containers = collect_docker_statuses()
    table = Table(title="Docker Containers")
    table.add_column("Name", style="bold")
    table.add_column("Status", style="cyan")
    table.add_column("Health", style="yellow")
    table.add_column("Image", style="dim")

    for c in containers:
        table.add_row(c.name, c.status, c.health, c.image)
    console.print(table)


@app.command()
def start(config: str = "config.yaml"):
    """Start the OpsPilot 24/7 daemon (Telegram Bot + Background Monitoring)."""
    asyncio.run(run_daemon(config))


if __name__ == "__main__":
    app()
