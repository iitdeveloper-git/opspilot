import logging

from opspilot.core.executor import SafeOperationExecutor

logger = logging.getLogger("opspilot.automation.prune")


async def execute_auto_prune(executor: SafeOperationExecutor, current_disk_pct: float, threshold: float = 85.0) -> dict:
    if current_disk_pct < threshold:
        return {"pruned": False, "reason": f"Disk at {current_disk_pct}%, below {threshold}% threshold."}

    logger.warning(f"Disk usage at {current_disk_pct}% exceeds {threshold}%. Triggering auto-prune...")
    res = await executor.prune_docker()

    if not res.get("success"):
        # Do not report pruned=True when the operation actually failed
        logger.error(f"Auto-prune failed: {res.get('error', 'unknown error')}")
        return {"pruned": False, "reason": f"prune_docker() failed: {res.get('error', 'unknown error')}"}

    return {"pruned": True, "reclaimed_mb": res.get("reclaimed_mb", 0)}
