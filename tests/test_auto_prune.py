from unittest.mock import AsyncMock

import pytest

from opspilot.automation.auto_prune import execute_auto_prune
from opspilot.core.executor import SafeOperationExecutor


@pytest.mark.asyncio
async def test_execute_auto_prune_below_threshold():
    executor = SafeOperationExecutor()
    result = await execute_auto_prune(executor, current_disk_pct=75.0, threshold=85.0)
    assert result["pruned"] is False
    assert "below 85.0% threshold" in result["reason"]


@pytest.mark.asyncio
async def test_execute_auto_prune_success():
    executor = SafeOperationExecutor()
    executor.prune_docker = AsyncMock(return_value={"success": True, "reclaimed_mb": 512.5})

    result = await execute_auto_prune(executor, current_disk_pct=90.0, threshold=85.0)
    assert result["pruned"] is True
    assert result["reclaimed_mb"] == 512.5


@pytest.mark.asyncio
async def test_execute_auto_prune_failure():
    """Ensure failure in prune_docker does NOT report pruned=True."""
    executor = SafeOperationExecutor()
    executor.prune_docker = AsyncMock(return_value={"success": False, "error": "Docker socket timeout"})

    result = await execute_auto_prune(executor, current_disk_pct=92.0, threshold=85.0)
    assert result["pruned"] is False
    assert "prune_docker() failed" in result["reason"]
