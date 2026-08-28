import pytest

from opspilot.core.executor import SafeOperationExecutor


@pytest.mark.asyncio
async def test_safe_executor_unknown_container_restart():
    executor = SafeOperationExecutor()
    res = await executor.restart_container("non_existent_container_xyz")
    assert res["success"] is False
    assert "not found" in res["message"].lower() or "error" in res["message"].lower()


@pytest.mark.asyncio
async def test_safe_executor_unknown_container_stop():
    executor = SafeOperationExecutor()
    res = await executor.stop_container("non_existent_container_xyz")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_safe_executor_unknown_container_start():
    executor = SafeOperationExecutor()
    res = await executor.start_container("non_existent_container_xyz")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_safe_executor_unknown_container_logs():
    executor = SafeOperationExecutor()
    logs = await executor.get_container_logs("non_existent_container_xyz")
    assert "error" in logs.lower() or "not found" in logs.lower()
