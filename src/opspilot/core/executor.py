import asyncio
import logging
from typing import Any

try:
    import docker
    from docker.errors import APIError, NotFound
except ImportError:
    docker = None
    NotFound = Exception
    APIError = Exception

logger = logging.getLogger("opspilot.executor")


class SafeOperationExecutor:
    """
    Deterministic safe executor. NEVER runs arbitrary shell strings.
    All blocking Docker SDK calls are executed in asyncio.to_thread.
    """

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
            container = await asyncio.to_thread(self.client.containers.get, container_name)
            await asyncio.to_thread(container.restart, timeout=10)
            return {"success": True, "message": f"Container {container_name} restarted successfully."}
        except NotFound:
            return {"success": False, "message": f"Container {container_name} not found."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def stop_container(self, container_name: str) -> dict[str, Any]:
        try:
            self._ensure_docker()
            container = await asyncio.to_thread(self.client.containers.get, container_name)
            await asyncio.to_thread(container.stop, timeout=10)
            return {"success": True, "message": f"Container {container_name} stopped."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def start_container(self, container_name: str) -> dict[str, Any]:
        try:
            self._ensure_docker()
            container = await asyncio.to_thread(self.client.containers.get, container_name)
            await asyncio.to_thread(container.start)
            return {"success": True, "message": f"Container {container_name} started."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def get_container_logs(self, container_name: str, tail: int = 50) -> str:
        try:
            self._ensure_docker()
            container = await asyncio.to_thread(self.client.containers.get, container_name)
            logs = await asyncio.to_thread(container.logs, tail=tail, timestamps=True)
            return logs.decode("utf-8", errors="replace")
        except Exception as e:
            return f"Error fetching logs: {e}"

    async def prune_docker(self) -> dict[str, Any]:
        try:
            self._ensure_docker()
            images = await asyncio.to_thread(self.client.images.prune)
            containers = await asyncio.to_thread(self.client.containers.prune)
            reclaimed = images.get("SpaceReclaimed", 0) + containers.get("SpaceReclaimed", 0)
            mb = reclaimed / (1024 * 1024)
            return {"success": True, "reclaimed_mb": round(mb, 2)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def run_command(self, cmd: list[str]) -> dict[str, Any]:
        """
        Compatibility dispatcher for bot legacy command arrays.
        Safely routes structured commands without arbitrary shell execution.
        """
        if not cmd:
            return {"success": False, "returncode": 1, "message": "Empty command."}

        if cmd[0] == "docker" and len(cmd) >= 2:
            sub = cmd[1]
            if sub == "logs":
                tail = 50
                target = cmd[-1]
                if "--tail" in cmd:
                    try:
                        idx = cmd.index("--tail")
                        tail = int(cmd[idx + 1])
                    except (ValueError, IndexError):
                        tail = 50
                logs = await self.get_container_logs(target, tail=tail)
                return {
                    "success": True,
                    "returncode": 0,
                    "stdout": logs,
                    "stderr": "",
                    "output": logs,
                }

            elif sub == "restart" and len(cmd) >= 3:
                res = await self.restart_container(cmd[2])
                ok = res.get("success", False)
                return {
                    "success": ok,
                    "returncode": 0 if ok else 1,
                    "stdout": res.get("message", ""),
                    "stderr": "" if ok else res.get("message", ""),
                }

            elif sub == "system" and len(cmd) >= 3 and cmd[2] == "prune":
                res = await self.prune_docker()
                ok = res.get("success", False)
                msg = (
                    f"Docker cache pruned. Reclaimed {res.get('reclaimed_mb', 0)} MB."
                    if ok
                    else res.get("error", "Prune failed.")
                )
                return {
                    "success": ok,
                    "returncode": 0 if ok else 1,
                    "stdout": msg if ok else "",
                    "stderr": "" if ok else msg,
                    "output": msg,
                }

        return {"success": False, "returncode": 1, "message": f"Command '{' '.join(cmd)}' is not permitted."}
