import pytest
from opspilot.core.executor import SafeOperationExecutor


@pytest.mark.asyncio
async def test_safe_executor_unknown_container():
    executor = SafeOperationExecutor()
    res = await executor.restart_container("non_existent_container_xyz")
    assert res["success"] is False
    assert "not found" in res["message"].lower()
