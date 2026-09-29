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


@pytest.mark.asyncio
async def test_safe_executor_run_command_routing():
    executor = SafeOperationExecutor()
    # Test logs routing
    res = await executor.run_command(["docker", "logs", "--tail", "20", "nonexistent"])
    assert "Error fetching logs" in res.get("output", "") or not res.get("success")

    # Test unknown command rejection
    bad_res = await executor.run_command(["rm", "-rf", "/"])
    assert not bad_res.get("success")
    assert "is not permitted" in bad_res.get("message", "")
