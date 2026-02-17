"""Tests for the Shell Executor module."""

import asyncio
import platform
import pytest

from executor import (
    ShellExecutor,
    ExecutionMode,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    command_matches_whitelist,
    detect_shell,
    execution_result_to_dict,
    DEFAULT_WHITELIST,
)


# ---------------------------------------------------------------------------
# Whitelist matching
# ---------------------------------------------------------------------------


class TestWhitelistMatching:
    def test_exact_match(self):
        assert command_matches_whitelist("echo", ["echo"])

    def test_prefix_match(self):
        assert command_matches_whitelist("echo hello", ["echo"])

    def test_no_match(self):
        assert not command_matches_whitelist("rm -rf /", ["echo", "ls"])

    def test_case_insensitive(self):
        assert command_matches_whitelist("ECHO hello", ["echo"])

    def test_multi_word_prefix(self):
        assert command_matches_whitelist("git status --short", ["git status"])
        assert not command_matches_whitelist("git push", ["git status"])

    def test_leading_whitespace(self):
        assert command_matches_whitelist("  ls -la", ["ls"])

    def test_empty_command(self):
        assert not command_matches_whitelist("", ["echo"])

    def test_default_whitelist_has_entries(self):
        assert len(DEFAULT_WHITELIST) > 10


# ---------------------------------------------------------------------------
# Shell detection
# ---------------------------------------------------------------------------


class TestShellDetection:
    def test_returns_tuple(self):
        name, cmd = detect_shell()
        assert isinstance(name, str)
        assert isinstance(cmd, list)
        assert len(cmd) >= 2

    def test_unix_shell(self):
        if platform.system() != "Windows":
            name, cmd = detect_shell()
            assert name in ("bash", "sh")
            assert cmd[-1] == "-c"


# ---------------------------------------------------------------------------
# Executor — YOLO mode (no approval needed)
# ---------------------------------------------------------------------------


class TestExecutorYolo:
    @pytest.fixture
    def executor(self):
        return ShellExecutor(mode=ExecutionMode.YOLO)

    @pytest.mark.asyncio
    async def test_simple_echo(self, executor):
        req = ExecutionRequest(id="t1", command="echo hello")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.COMPLETED
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_stderr_capture(self, executor):
        req = ExecutionRequest(id="t2", command="echo err >&2")
        result = await executor.execute(req)
        assert "err" in result.stderr

    @pytest.mark.asyncio
    async def test_nonzero_exit(self, executor):
        req = ExecutionRequest(id="t3", command="exit 42")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.FAILED
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_timeout(self, executor):
        req = ExecutionRequest(id="t4", command="sleep 60", timeout_seconds=1)
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_empty_command(self, executor):
        req = ExecutionRequest(id="t5", command="")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_streaming_callback(self, executor):
        chunks = []
        executor.stream_cb = lambda rid, stype, data: chunks.append((rid, stype, data))
        req = ExecutionRequest(id="t6", command="echo streamed")
        await executor.execute(req)
        stdout_chunks = [c for c in chunks if c[1] == "stdout"]
        assert any("streamed" in c[2] for c in stdout_chunks)

    @pytest.mark.asyncio
    async def test_cwd(self, executor):
        req = ExecutionRequest(id="t7", command="pwd", cwd="/tmp")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.COMPLETED
        # On some systems /tmp may be a symlink
        assert "tmp" in result.stdout.lower()

    @pytest.mark.asyncio
    async def test_duration_tracked(self, executor):
        req = ExecutionRequest(id="t8", command="echo fast")
        result = await executor.execute(req)
        assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Executor — ASSISTED mode (all commands need approval)
# ---------------------------------------------------------------------------


class TestExecutorAssisted:
    @pytest.mark.asyncio
    async def test_rejected_without_callback(self):
        executor = ShellExecutor(mode=ExecutionMode.ASSISTED)
        req = ExecutionRequest(id="a1", command="echo nope")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.REJECTED

    @pytest.mark.asyncio
    async def test_approved_with_callback(self):
        executor = ShellExecutor(
            mode=ExecutionMode.ASSISTED,
            approval_cb=lambda rid, cmd: True,
        )
        req = ExecutionRequest(id="a2", command="echo yes")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_rejected_with_callback(self):
        executor = ShellExecutor(
            mode=ExecutionMode.ASSISTED,
            approval_cb=lambda rid, cmd: False,
        )
        req = ExecutionRequest(id="a3", command="echo no")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.REJECTED

    @pytest.mark.asyncio
    async def test_async_approval_callback(self):
        async def approve(rid, cmd):
            return True

        executor = ShellExecutor(
            mode=ExecutionMode.ASSISTED,
            approval_cb=approve,
        )
        req = ExecutionRequest(id="a4", command="echo async")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Executor — WHITELIST mode
# ---------------------------------------------------------------------------


class TestExecutorWhitelist:
    @pytest.mark.asyncio
    async def test_whitelisted_runs_without_approval(self):
        executor = ShellExecutor(
            mode=ExecutionMode.WHITELIST,
            whitelist=["echo"],
        )
        req = ExecutionRequest(id="w1", command="echo ok")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_non_whitelisted_rejected_without_callback(self):
        executor = ShellExecutor(
            mode=ExecutionMode.WHITELIST,
            whitelist=["echo"],
        )
        req = ExecutionRequest(id="w2", command="rm -rf /")
        result = await executor.execute(req)
        assert result.status == ExecutionStatus.REJECTED

    @pytest.mark.asyncio
    async def test_non_whitelisted_approved_with_callback(self):
        executor = ShellExecutor(
            mode=ExecutionMode.WHITELIST,
            whitelist=["echo"],
            approval_cb=lambda rid, cmd: True,
        )
        req = ExecutionRequest(id="w3", command="rm -rf /nonexistent 2>/dev/null; echo done")
        result = await executor.execute(req)
        assert result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_result_to_dict(self):
        r = ExecutionResult(
            command="echo hi",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="hi\n",
            stderr="",
            duration_ms=42,
        )
        d = execution_result_to_dict(r)
        assert d["status"] == "completed"
        assert d["exit_code"] == 0
        assert d["duration_ms"] == 42


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_running(self):
        executor = ShellExecutor(mode=ExecutionMode.YOLO)
        req = ExecutionRequest(id="c1", command="sleep 60", timeout_seconds=30)

        async def run_and_cancel():
            task = asyncio.create_task(executor.execute(req))
            await asyncio.sleep(0.3)
            cancelled = await executor.cancel("c1")
            assert cancelled
            return await task

        result = await run_and_cancel()
        assert result.status in (ExecutionStatus.FAILED, ExecutionStatus.CANCELLED, ExecutionStatus.TIMEOUT)

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        executor = ShellExecutor(mode=ExecutionMode.YOLO)
        assert not await executor.cancel("nonexistent")
