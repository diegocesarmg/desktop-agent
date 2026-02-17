"""Tests for the enhanced Shell Executor module."""
import asyncio
import pytest
from shell_executor import (
    ShellExecutor, ExecutionMode, ExecutionRequest, ExecutionStatus,
    MissionPermissionManager, command_matches_whitelist, detect_available_shells,
    resolve_shell, ShellType, execution_result_to_dict,
)


class TestShellDetection:
    def test_detect_available(self):
        shells = detect_available_shells()
        assert len(shells) > 0

    def test_resolve_auto(self):
        shells = detect_available_shells()
        st, cmd = resolve_shell(ShellType.AUTO, shells)
        assert len(cmd) >= 2


class TestMissionPermissions:
    def test_no_mission(self):
        mgr = MissionPermissionManager()
        ok, _ = mgr.check_permission(None, "echo hi")
        assert ok

    def test_blocked(self):
        mgr = MissionPermissionManager()
        mgr.set_permissions("m1", {"blocked_commands": ["rm"]})
        ok, _ = mgr.check_permission("m1", "rm -rf /")
        assert not ok

    def test_allowed_list(self):
        mgr = MissionPermissionManager()
        mgr.set_permissions("m1", {"allowed_commands": ["echo", "ls"]})
        ok, _ = mgr.check_permission("m1", "echo hi")
        assert ok
        ok, _ = mgr.check_permission("m1", "rm file")
        assert not ok

    def test_max_timeout(self):
        mgr = MissionPermissionManager()
        mgr.set_permissions("m1", {"max_timeout": 10})
        assert mgr.get_max_timeout("m1") == 10

    def test_execution_mode_override(self):
        mgr = MissionPermissionManager()
        mgr.set_permissions("m1", {"execution_mode": "yolo"})
        assert mgr.get_execution_mode("m1") == ExecutionMode.YOLO


class TestShellExecutorYolo:
    @pytest.fixture
    def executor(self):
        return ShellExecutor(mode=ExecutionMode.YOLO)

    @pytest.mark.asyncio
    async def test_echo(self, executor):
        req = ExecutionRequest(id="t1", command="echo hello")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.COMPLETED
        assert "hello" in result.stdout
        assert result.shell_type

    @pytest.mark.asyncio
    async def test_timeout(self, executor):
        req = ExecutionRequest(id="t2", command="sleep 60", timeout_seconds=1)
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_mission_permission_denied(self, executor):
        executor.permission_manager.set_permissions("m1", {"blocked_commands": ["echo"]})
        req = ExecutionRequest(id="t3", command="echo blocked", mission_id="m1")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_serialization(self, executor):
        req = ExecutionRequest(id="t4", command="echo hi")
        result = await executor.execute(req)
        d = execution_result_to_dict(result)
        assert d["status"] == "completed"
        assert "mission_id" in d
