"""GCC Desktop Agent — Shell Executor.

Executes shell commands locally with real-time output streaming.
Supports three security modes:
  - assisted: every command requires explicit user approval
  - yolo: all commands run without approval
  - whitelist: only pre-approved command prefixes run automatically;
               everything else requires approval

Output is streamed in real-time via a callback, suitable for
forwarding over WebSocket to the GCC server.
"""

import asyncio
import enum
import logging
import os
import platform
import re
import shlex
import signal
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("gcc-agent.executor")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ExecutionMode(enum.Enum):
    ASSISTED = "assisted"
    YOLO = "yolo"
    WHITELIST = "whitelist"


class ExecutionStatus(enum.Enum):
    PENDING = "pending"          # waiting for approval
    APPROVED = "approved"        # approved, about to run
    REJECTED = "rejected"        # user rejected
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ExecutionResult:
    """Result of a single command execution."""
    command: str
    status: ExecutionStatus
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class ExecutionRequest:
    """Incoming request to execute a command."""
    id: str
    command: str
    cwd: Optional[str] = None
    env: Optional[dict] = None
    timeout_seconds: int = 300
    shell: Optional[str] = None  # override auto-detected shell


# Stream callback signature: (request_id, stream_type, chunk)
# stream_type is "stdout", "stderr", or "status"
StreamCallback = Callable[[str, str, str], None]

# Approval callback signature: (request_id, command) -> bool
# Returns True to approve, False to reject.  May be async.
ApprovalCallback = Callable[[str, str], bool]


# ---------------------------------------------------------------------------
# Default whitelist — safe, read-only commands
# ---------------------------------------------------------------------------

DEFAULT_WHITELIST: list[str] = [
    "echo",
    "cat",
    "ls",
    "dir",
    "pwd",
    "whoami",
    "hostname",
    "date",
    "uname",
    "type",
    "which",
    "where",
    "env",
    "set",
    "printenv",
    "ping",
    "nslookup",
    "dig",
    "curl",
    "head",
    "tail",
    "wc",
    "df",
    "free",
    "uptime",
    "python --version",
    "python3 --version",
    "node --version",
    "git status",
    "git log",
    "git diff",
    "git branch",
    "pip list",
    "pip show",
    "npm list",
    "systeminfo",
    "ver",
]

# ---------------------------------------------------------------------------
# Shell detection
# ---------------------------------------------------------------------------


def detect_shell() -> tuple[str, list[str]]:
    """Return (shell_name, [shell_binary, *flags]) for the current platform.

    On Windows, prefers PowerShell if available, falls back to cmd.exe.
    On Linux/macOS, uses /bin/bash (or /bin/sh).
    WSL2 detection: if running inside WSL the platform is Linux but we note it.
    """
    system = platform.system().lower()

    if system == "windows":
        # Try PowerShell 7+ first, then Windows PowerShell, then cmd
        for ps in ("pwsh", "powershell"):
            if _which(ps):
                return (ps, [ps, "-NoProfile", "-NonInteractive", "-Command"])
        return ("cmd", ["cmd.exe", "/C"])

    # Linux / macOS / WSL2
    bash = "/bin/bash"
    if os.path.exists(bash):
        return ("bash", [bash, "-c"])
    return ("sh", ["/bin/sh", "-c"])


def is_wsl() -> bool:
    """Detect if we're running inside WSL2."""
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _which(name: str) -> Optional[str]:
    """Cross-platform which."""
    import shutil
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Whitelist matching
# ---------------------------------------------------------------------------


def command_matches_whitelist(command: str, whitelist: list[str]) -> bool:
    """Check whether *command* starts with any whitelisted prefix.

    Matching is case-insensitive and ignores leading whitespace.
    A whitelist entry like ``git status`` matches ``git status --short``
    but NOT ``git push``.
    """
    cmd = command.strip().lower()
    for prefix in whitelist:
        p = prefix.strip().lower()
        if cmd == p or cmd.startswith(p + " ") or cmd.startswith(p + "\t"):
            return True
    return False


# ---------------------------------------------------------------------------
# ShellExecutor
# ---------------------------------------------------------------------------


class ShellExecutor:
    """Executes shell commands with streaming output and configurable security.

    Parameters
    ----------
    mode : ExecutionMode
        Security mode controlling approval flow.
    stream_cb : StreamCallback, optional
        Called with (request_id, stream_type, chunk) for real-time output.
    approval_cb : ApprovalCallback, optional
        Called when a command needs user approval (assisted / whitelist miss).
        Must return True/False.  Can be a coroutine function.
    whitelist : list[str], optional
        Command prefixes allowed without approval in WHITELIST mode.
    shell_override : str, optional
        Force a specific shell instead of auto-detecting.
    max_output_bytes : int
        Truncate captured stdout/stderr beyond this limit (default 5 MB).
    """

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.ASSISTED,
        stream_cb: Optional[StreamCallback] = None,
        approval_cb: Optional[ApprovalCallback] = None,
        whitelist: Optional[list[str]] = None,
        shell_override: Optional[str] = None,
        max_output_bytes: int = 5 * 1024 * 1024,
    ):
        self.mode = mode
        self.stream_cb = stream_cb
        self.approval_cb = approval_cb
        self.whitelist = whitelist if whitelist is not None else list(DEFAULT_WHITELIST)
        self.max_output_bytes = max_output_bytes

        if shell_override:
            self._shell_name = shell_override
            self._shell_cmd = [shell_override, "-c"]
        else:
            self._shell_name, self._shell_cmd = detect_shell()

        # Active processes keyed by request id — for cancellation
        self._active: dict[str, asyncio.subprocess.Process] = {}

        logger.info(
            "ShellExecutor ready  mode=%s  shell=%s  wsl=%s  whitelist_entries=%d",
            self.mode.value,
            self._shell_name,
            is_wsl(),
            len(self.whitelist),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute a command request through the approval + execution pipeline."""
        command = request.command.strip()
        if not command:
            return ExecutionResult(
                command=command,
                status=ExecutionStatus.FAILED,
                error="Empty command",
            )

        # --- Approval gate ---
        needs_approval = self._needs_approval(command)
        if needs_approval:
            self._emit(request.id, "status", "awaiting_approval")
            approved = await self._get_approval(request.id, command)
            if not approved:
                self._emit(request.id, "status", "rejected")
                return ExecutionResult(
                    command=command, status=ExecutionStatus.REJECTED
                )
            self._emit(request.id, "status", "approved")

        # --- Execute ---
        return await self._run(request)

    async def cancel(self, request_id: str) -> bool:
        """Cancel a running command by request id.  Returns True if killed."""
        proc = self._active.get(request_id)
        if proc and proc.returncode is None:
            logger.info("Cancelling request %s (pid %d)", request_id, proc.pid)
            try:
                proc.terminate()
                # Give it a moment, then kill hard
                await asyncio.sleep(0.5)
                if proc.returncode is None:
                    proc.kill()
            except ProcessLookupError:
                pass
            return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _needs_approval(self, command: str) -> bool:
        if self.mode == ExecutionMode.YOLO:
            return False
        if self.mode == ExecutionMode.ASSISTED:
            return True
        # WHITELIST mode
        return not command_matches_whitelist(command, self.whitelist)

    async def _get_approval(self, request_id: str, command: str) -> bool:
        if self.approval_cb is None:
            logger.warning("No approval callback set — rejecting command")
            return False
        result = self.approval_cb(request_id, command)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _run(self, request: ExecutionRequest) -> ExecutionResult:
        command = request.command.strip()
        shell_cmd = self._shell_cmd.copy()

        # For PowerShell, the command is passed as a single argument.
        # For bash/sh -c, same.  For cmd /C, same.
        shell_cmd.append(command)

        self._emit(request.id, "status", "running")
        start = time.monotonic()

        env = os.environ.copy()
        if request.env:
            env.update(request.env)

        cwd = request.cwd or None

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                # Start new process group so we can kill the tree
                preexec_fn=os.setsid if platform.system() != "Windows" else None,
            )
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                command=command,
                status=ExecutionStatus.FAILED,
                duration_ms=duration,
                error=str(exc),
            )

        self._active[request.id] = proc

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_len = 0
        stderr_len = 0

        async def _read_stream(
            stream: asyncio.StreamReader,
            chunks: list[bytes],
            name: str,
        ):
            nonlocal stdout_len, stderr_len
            current_len_ref = "stdout_len" if name == "stdout" else "stderr_len"
            while True:
                # Read in small chunks for real-time streaming
                chunk = await stream.read(4096)
                if not chunk:
                    break
                cur_len = stdout_len if name == "stdout" else stderr_len
                if cur_len < self.max_output_bytes:
                    keep = min(len(chunk), self.max_output_bytes - cur_len)
                    chunks.append(chunk[:keep])
                if name == "stdout":
                    stdout_len += len(chunk)
                else:
                    stderr_len += len(chunk)

                # Stream callback with decoded text
                try:
                    text = chunk.decode("utf-8", errors="replace")
                    self._emit(request.id, name, text)
                except Exception:
                    pass

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stream(proc.stdout, stdout_chunks, "stdout"),
                    _read_stream(proc.stderr, stderr_chunks, "stderr"),
                    proc.wait(),
                ),
                timeout=request.timeout_seconds,
            )
            status = ExecutionStatus.COMPLETED if proc.returncode == 0 else ExecutionStatus.FAILED
        except asyncio.TimeoutError:
            logger.warning("Command timed out after %ds: %s", request.timeout_seconds, command)
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

        stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        self._emit(request.id, "status", status.value)

        return ExecutionResult(
            command=command,
            status=status,
            exit_code=proc.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=duration,
        )

    def _emit(self, request_id: str, stream_type: str, data: str):
        if self.stream_cb:
            try:
                self.stream_cb(request_id, stream_type, data)
            except Exception as exc:
                logger.debug("Stream callback error: %s", exc)


# ---------------------------------------------------------------------------
# WebSocket integration helpers
# ---------------------------------------------------------------------------


def create_executor_from_config(config: dict) -> ShellExecutor:
    """Create a ShellExecutor from the agent's config dict.

    Reads:
      - executor_mode: "assisted" | "yolo" | "whitelist"  (default: assisted)
      - executor_whitelist: list of command prefixes       (default: DEFAULT_WHITELIST)
      - executor_timeout: default timeout in seconds       (default: 300)
      - executor_max_output: max output bytes              (default: 5MB)
    """
    mode_str = config.get("executor_mode", "assisted")
    try:
        mode = ExecutionMode(mode_str)
    except ValueError:
        logger.warning("Invalid executor_mode '%s', defaulting to assisted", mode_str)
        mode = ExecutionMode.ASSISTED

    whitelist = config.get("executor_whitelist", None)
    max_output = config.get("executor_max_output", 5 * 1024 * 1024)

    return ShellExecutor(
        mode=mode,
        whitelist=whitelist,
        max_output_bytes=max_output,
    )


def execution_result_to_dict(result: ExecutionResult) -> dict:
    """Serialize an ExecutionResult for JSON/WebSocket transmission."""
    return {
        "command": result.command,
        "status": result.status.value,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "error": result.error,
    }
