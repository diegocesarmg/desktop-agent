"""GCC Desktop Agent — Shell Executor with PS/CMD/WSL2 support.

Task #3: Enhanced shell command execution supporting PowerShell, CMD, and WSL2.
Sandboxed execution with timeout, output capture/streaming, and per-mission
permission control.
"""

import asyncio
import enum
import logging
import os
import platform
import shutil
import signal
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("gcc-agent.shell")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ShellType(enum.Enum):
    """Supported shell types."""
    POWERSHELL = "powershell"
    CMD = "cmd"
    WSL2 = "wsl2"
    BASH = "bash"
    SH = "sh"
    AUTO = "auto"


class ExecutionMode(enum.Enum):
    ASSISTED = "assisted"
    YOLO = "yolo"
    WHITELIST = "whitelist"


class ExecutionStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"


@dataclass
class ExecutionResult:
    command: str
    status: ExecutionStatus
    shell_type: str = ""
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: Optional[str] = None
    mission_id: Optional[str] = None


@dataclass
class ExecutionRequest:
    id: str
    command: str
    mission_id: Optional[str] = None
    cwd: Optional[str] = None
    env: Optional[dict] = None
    timeout_seconds: int = 300
    shell_type: ShellType = ShellType.AUTO


StreamCallback = Callable[[str, str, str], None]
ApprovalCallback = Callable[[str, str], bool]


# ---------------------------------------------------------------------------
# Default whitelist
# ---------------------------------------------------------------------------

DEFAULT_WHITELIST: list[str] = [
    "echo", "cat", "ls", "dir", "pwd", "whoami", "hostname", "date",
    "uname", "type", "which", "where", "env", "set", "printenv",
    "ping", "nslookup", "dig", "curl", "head", "tail", "wc", "df",
    "free", "uptime", "python --version", "python3 --version",
    "node --version", "git status", "git log", "git diff", "git branch",
    "pip list", "pip show", "npm list", "systeminfo", "ver",
    "Get-Process", "Get-Service", "Get-Date", "Get-ChildItem",
    "wsl --list", "wsl --status",
]


# ---------------------------------------------------------------------------
# Shell detection & resolution
# ---------------------------------------------------------------------------

def detect_available_shells() -> dict[ShellType, list[str]]:
    """Detect all available shells on the system."""
    available = {}
    system = platform.system().lower()

    if system == "windows":
        for ps in ("pwsh", "powershell"):
            if shutil.which(ps):
                available[ShellType.POWERSHELL] = [ps, "-NoProfile", "-NonInteractive", "-Command"]
                break
        available[ShellType.CMD] = ["cmd.exe", "/C"]
        # Check for WSL2
        if shutil.which("wsl"):
            available[ShellType.WSL2] = ["wsl", "--", "bash", "-c"]
    else:
        if os.path.exists("/bin/bash"):
            available[ShellType.BASH] = ["/bin/bash", "-c"]
        if os.path.exists("/bin/sh"):
            available[ShellType.SH] = ["/bin/sh", "-c"]
        # Check WSL2 from inside Linux (running within WSL)
        if _is_wsl():
            available[ShellType.WSL2] = ["/bin/bash", "-c"]
        # Can also invoke PowerShell if pwsh is installed on Linux
        if shutil.which("pwsh"):
            available[ShellType.POWERSHELL] = ["pwsh", "-NoProfile", "-NonInteractive", "-Command"]

    return available


def _is_wsl() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def resolve_shell(shell_type: ShellType, available: dict[ShellType, list[str]]) -> tuple[ShellType, list[str]]:
    """Resolve shell type to actual command, with AUTO fallback."""
    if shell_type != ShellType.AUTO:
        if shell_type in available:
            return shell_type, available[shell_type]
        raise ValueError(f"Shell {shell_type.value} not available. Available: {[s.value for s in available]}")

    # AUTO: prefer PowerShell on Windows, bash on Unix
    system = platform.system().lower()
    if system == "windows":
        for pref in (ShellType.POWERSHELL, ShellType.CMD):
            if pref in available:
                return pref, available[pref]
    else:
        for pref in (ShellType.BASH, ShellType.SH):
            if pref in available:
                return pref, available[pref]

    if available:
        first = next(iter(available))
        return first, available[first]
    raise RuntimeError("No shell available")


def command_matches_whitelist(command: str, whitelist: list[str]) -> bool:
    cmd = command.strip().lower()
    for prefix in whitelist:
        p = prefix.strip().lower()
        if cmd == p or cmd.startswith(p + " ") or cmd.startswith(p + "\t"):
            return True
    return False


# ---------------------------------------------------------------------------
# Mission Permission Manager
# ---------------------------------------------------------------------------

class MissionPermissionManager:
    """Controls which commands a mission is allowed to execute.

    Each mission can have:
      - allowed_commands: list of command prefixes allowed
      - blocked_commands: list of command prefixes explicitly blocked
      - execution_mode: override the global execution mode per mission
      - max_timeout: maximum timeout allowed for this mission
    """

    def __init__(self):
        self._permissions: dict[str, dict] = {}

    def set_permissions(self, mission_id: str, permissions: dict):
        self._permissions[mission_id] = permissions

    def remove_permissions(self, mission_id: str):
        self._permissions.pop(mission_id, None)

    def get_permissions(self, mission_id: str) -> Optional[dict]:
        return self._permissions.get(mission_id)

    def check_permission(self, mission_id: Optional[str], command: str) -> tuple[bool, str]:
        """Check if command is allowed for mission. Returns (allowed, reason)."""
        if not mission_id:
            return True, "no mission context"

        perms = self._permissions.get(mission_id)
        if not perms:
            return True, "no restrictions for mission"

        cmd = command.strip().lower()

        # Check blocked first
        blocked = perms.get("blocked_commands", [])
        for pattern in blocked:
            p = pattern.strip().lower()
            if cmd == p or cmd.startswith(p + " "):
                return False, f"command matches blocked pattern: {pattern}"

        # If allowed list exists, command must match
        allowed = perms.get("allowed_commands")
        if allowed is not None:
            for pattern in allowed:
                p = pattern.strip().lower()
                if cmd == p or cmd.startswith(p + " "):
                    return True, f"matches allowed pattern: {pattern}"
            return False, "command not in allowed list for mission"

        return True, "allowed by default"

    def get_max_timeout(self, mission_id: Optional[str]) -> Optional[int]:
        if not mission_id:
            return None
        perms = self._permissions.get(mission_id)
        if not perms:
            return None
        return perms.get("max_timeout")

    def get_execution_mode(self, mission_id: Optional[str]) -> Optional[ExecutionMode]:
        if not mission_id:
            return None
        perms = self._permissions.get(mission_id)
        if not perms:
            return None
        mode_str = perms.get("execution_mode")
        if mode_str:
            try:
                return ExecutionMode(mode_str)
            except ValueError:
                pass
        return None


# ---------------------------------------------------------------------------
# Enhanced ShellExecutor
# ---------------------------------------------------------------------------

class ShellExecutor:
    """Shell executor with PS/CMD/WSL2 support and mission-based permissions."""

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.ASSISTED,
        stream_cb: Optional[StreamCallback] = None,
        approval_cb: Optional[ApprovalCallback] = None,
        whitelist: Optional[list[str]] = None,
        max_output_bytes: int = 5 * 1024 * 1024,
        permission_manager: Optional[MissionPermissionManager] = None,
    ):
        self.mode = mode
        self.stream_cb = stream_cb
        self.approval_cb = approval_cb
        self.whitelist = whitelist if whitelist is not None else list(DEFAULT_WHITELIST)
        self.max_output_bytes = max_output_bytes
        self.permission_manager = permission_manager or MissionPermissionManager()
        self.available_shells = detect_available_shells()
        self._active: dict[str, asyncio.subprocess.Process] = {}

        logger.info(
            "ShellExecutor ready  mode=%s  shells=%s  wsl=%s",
            self.mode.value,
            [s.value for s in self.available_shells],
            _is_wsl(),
        )

    def get_available_shells(self) -> list[str]:
        return [s.value for s in self.available_shells]

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        command = request.command.strip()
        if not command:
            return ExecutionResult(command=command, status=ExecutionStatus.FAILED, error="Empty command")

        # Mission permission check
        allowed, reason = self.permission_manager.check_permission(request.mission_id, command)
        if not allowed:
            self._emit(request.id, "status", "permission_denied")
            return ExecutionResult(
                command=command,
                status=ExecutionStatus.PERMISSION_DENIED,
                error=f"Permission denied: {reason}",
                mission_id=request.mission_id,
            )

        # Resolve shell
        try:
            shell_type, shell_cmd = resolve_shell(request.shell_type, self.available_shells)
        except (ValueError, RuntimeError) as e:
            return ExecutionResult(command=command, status=ExecutionStatus.FAILED, error=str(e))

        # Apply mission-level timeout cap
        timeout = request.timeout_seconds
        max_timeout = self.permission_manager.get_max_timeout(request.mission_id)
        if max_timeout and timeout > max_timeout:
            timeout = max_timeout

        # Determine effective execution mode
        effective_mode = self.permission_manager.get_execution_mode(request.mission_id) or self.mode

        # Approval gate
        needs_approval = self._needs_approval(command, effective_mode)
        if needs_approval:
            self._emit(request.id, "status", "awaiting_approval")
            approved = await self._get_approval(request.id, command)
            if not approved:
                self._emit(request.id, "status", "rejected")
                return ExecutionResult(command=command, status=ExecutionStatus.REJECTED, mission_id=request.mission_id)

        # Execute
        return await self._run(request, shell_type, shell_cmd, timeout)

    async def cancel(self, request_id: str) -> bool:
        proc = self._active.get(request_id)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.sleep(0.5)
                if proc.returncode is None:
                    proc.kill()
            except ProcessLookupError:
                pass
            return True
        return False

    def _needs_approval(self, command: str, mode: ExecutionMode) -> bool:
        if mode == ExecutionMode.YOLO:
            return False
        if mode == ExecutionMode.ASSISTED:
            return True
        return not command_matches_whitelist(command, self.whitelist)

    async def _get_approval(self, request_id: str, command: str) -> bool:
        if self.approval_cb is None:
            return False
        result = self.approval_cb(request_id, command)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _run(self, request: ExecutionRequest, shell_type: ShellType, shell_cmd: list[str], timeout: int) -> ExecutionResult:
        command = request.command.strip()
        full_cmd = shell_cmd + [command]

        self._emit(request.id, "status", "running")
        start = time.monotonic()

        env = os.environ.copy()
        if request.env:
            env.update(request.env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=request.cwd,
                env=env,
                preexec_fn=os.setsid if platform.system() != "Windows" else None,
            )
        except Exception as exc:
            return ExecutionResult(
                command=command, status=ExecutionStatus.FAILED,
                shell_type=shell_type.value, duration_ms=int((time.monotonic() - start) * 1000),
                error=str(exc), mission_id=request.mission_id,
            )

        self._active[request.id] = proc
        stdout_chunks, stderr_chunks = [], []

        async def _read(stream, chunks, name):
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                total = sum(len(c) for c in chunks)
                if total < self.max_output_bytes:
                    chunks.append(chunk[:self.max_output_bytes - total])
                try:
                    self._emit(request.id, name, chunk.decode("utf-8", errors="replace"))
                except Exception:
                    pass

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read(proc.stdout, stdout_chunks, "stdout"),
                    _read(proc.stderr, stderr_chunks, "stderr"),
                    proc.wait(),
                ),
                timeout=timeout,
            )
            status = ExecutionStatus.COMPLETED if proc.returncode == 0 else ExecutionStatus.FAILED
        except asyncio.TimeoutError:
            try:
                if platform.system() != "Windows":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, OSError):
                pass
            await proc.wait()
            status = ExecutionStatus.TIMEOUT
        except asyncio.CancelledError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            await proc.wait()
            status = ExecutionStatus.CANCELLED
        finally:
            self._active.pop(request.id, None)

        duration = int((time.monotonic() - start) * 1000)
        self._emit(request.id, "status", status.value)

        return ExecutionResult(
            command=command, status=status, shell_type=shell_type.value,
            exit_code=proc.returncode,
            stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
            duration_ms=duration, mission_id=request.mission_id,
        )

    def _emit(self, request_id: str, stream_type: str, data: str):
        if self.stream_cb:
            try:
                self.stream_cb(request_id, stream_type, data)
            except Exception:
                pass


def execution_result_to_dict(result: ExecutionResult) -> dict:
    return {
        "command": result.command,
        "status": result.status.value,
        "shell_type": result.shell_type,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "mission_id": result.mission_id,
    }
