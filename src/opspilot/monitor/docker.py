import logging
from typing import Any
from pydantic import BaseModel

try:
    import docker
except ImportError:
    docker = None

logger = logging.getLogger("opspilot.monitor.docker")


class ContainerStatus(BaseModel):
    id: str
    name: str
    image: str
    status: str  # running, exited, paused, restarting
    health: str  # healthy, unhealthy, starting, none
    created: str


def collect_docker_statuses() -> list[ContainerStatus]:
    if docker is None:
        return []
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        results = []
        for c in containers:
            health = "none"
            state = c.attrs.get("State", {})
            if "Health" in state:
                health = state["Health"].get("Status", "none")

            results.append(
                ContainerStatus(
                    id=c.short_id,
                    name=c.name,
                    image=c.image.tags[0] if c.image.tags else c.image.short_id,
                    status=c.status,
                    health=health,
                    created=c.attrs.get("Created", "")[:19],
                )
            )
        return results
    except Exception as e:
        logger.warning(f"Docker status collection failed: {e}")
        return []
