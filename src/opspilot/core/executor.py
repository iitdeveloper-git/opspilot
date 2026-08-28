import logging
import asyncio
from typing import Any

try:
    import docker
    from docker.errors import NotFound, APIError
except ImportError:
    docker = None
    NotFound = Exception
    APIError = Exception

logger = logging.getLogger("opspilot.executor")


class SafeOperationExecutor:
    """Deterministic safe executor. NEVER runs arbitrary shell strings."""

    def __init__(self):
        self.client = None
        if docker is not None:
            try:
                self.client = docker.from_env()
            except Exception as e:
                logger.warning(f"Docker client initialization deferred: {e}")

    def _ensure_docker(self):
        if docker is None:
            raise RuntimeError("docker SDK is not installed")
        if self.client is None:
            self.client = docker.from_env()

    async def restart_container(self, container_name: str) -> dict[str, Any]:
        try:
            self._ensure_docker()
            container = self.client.containers.get(container_name)
            container.restart(timeout=10)
            return {"success": True, "message": f"Container {container_name} restarted successfully."}
        except NotFound:
            return {"success": False, "message": f"Container {container_name} not found."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def stop_container(self, container_name: str) -> dict[str, Any]:
        try:
            self._ensure_docker()
            container = self.client.containers.get(container_name)
            container.stop(timeout=10)
            return {"success": True, "message": f"Container {container_name} stopped."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def start_container(self, container_name: str) -> dict[str, Any]:
        try:
            self._ensure_docker()
            container = self.client.containers.get(container_name)
            container.start()
            return {"success": True, "message": f"Container {container_name} started."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def get_container_logs(self, container_name: str, tail: int = 50) -> str:
        try:
            self._ensure_docker()
            container = self.client.containers.get(container_name)
            logs = container.logs(tail=tail, timestamps=True)
            return logs.decode("utf-8", errors="replace")
        except Exception as e:
            return f"Error fetching logs: {e}"

    async def prune_docker(self) -> dict[str, Any]:
        try:
            self._ensure_docker()
            images = self.client.images.prune()
            containers = self.client.containers.prune()
            reclaimed = images.get("SpaceReclaimed", 0) + containers.get("SpaceReclaimed", 0)
            mb = reclaimed / (1024 * 1024)
            return {"success": True, "reclaimed_mb": round(mb, 2)}
        except Exception as e:
            return {"success": False, "error": str(e)}
