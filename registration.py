"""GCC Desktop Agent — Multi-desktop Registration.

Task #14: Agents register with unique label, dashboard shows list,
each with independent status. Registration endpoint on the GCC API side.
"""

import hashlib
import json
import logging
import platform
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("gcc-agent.registration")

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    _requests = None
    HAS_REQUESTS = False


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class AgentInfo:
    """Information about this desktop agent instance."""
    agent_id: str
    label: str
    hostname: str = ""
    platform_os: str = ""
    platform_version: str = ""
    python_version: str = ""
    ip_address: str = ""
    capabilities: list[str] = field(default_factory=list)
    version: str = "0.2.0"
    registered_at: float = 0.0
    last_heartbeat: float = 0.0
    status: str = "online"  # online, offline, busy, error
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "label": self.label,
            "hostname": self.hostname,
            "platform": self.platform_os,
            "platform_version": self.platform_version,
            "python_version": self.python_version,
            "ip_address": self.ip_address,
            "capabilities": self.capabilities,
            "version": self.version,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
            "status": self.status,
            "metadata": self.metadata,
        }


def generate_agent_id(label: str) -> str:
    """Generate a deterministic agent ID from label + machine info."""
    machine_info = f"{platform.node()}-{platform.machine()}-{platform.system()}"
    raw = f"{label}-{machine_info}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def detect_capabilities() -> list[str]:
    """Detect what this agent can do."""
    caps = ["shell_executor"]

    try:
        import pyautogui
        caps.append("desktop_control")
        caps.append("screenshot")
    except ImportError:
        pass

    if platform.system() == "Windows":
        caps.extend(["powershell", "cmd"])
        import shutil
        if shutil.which("wsl"):
            caps.append("wsl2")
    else:
        caps.append("bash")
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    caps.append("wsl2")
        except OSError:
            pass
        import shutil
        if shutil.which("pwsh"):
            caps.append("powershell")

    return caps


def get_local_ip() -> str:
    """Best-effort local IP detection."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Registration Manager
# ---------------------------------------------------------------------------

class RegistrationManager:
    """Handles agent registration, heartbeat, and status with the GCC API."""

    def __init__(self, api_url: str, api_key: str, label: str, agent_id: Optional[str] = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.label = label
        self.agent_id = agent_id or generate_agent_id(label)
        self._agent_info: Optional[AgentInfo] = None
        self._registered = False

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def agent_info(self) -> Optional[AgentInfo]:
        return self._agent_info

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def build_agent_info(self) -> AgentInfo:
        """Build agent info from current system."""
        return AgentInfo(
            agent_id=self.agent_id,
            label=self.label,
            hostname=platform.node(),
            platform_os=platform.system(),
            platform_version=platform.version(),
            python_version=platform.python_version(),
            ip_address=get_local_ip(),
            capabilities=detect_capabilities(),
            registered_at=time.time(),
            last_heartbeat=time.time(),
            status="online",
        )

    def register(self) -> tuple[bool, str]:
        """Register this agent with the GCC API.

        Returns (success, message).
        """
        self._agent_info = self.build_agent_info()

        if not HAS_REQUESTS:
            logger.warning("requests not installed, registration skipped")
            return False, "requests library not installed"

        try:
            resp = _requests.post(
                f"{self.api_url}/api/agents/register",
                headers=self._headers(),
                json=self._agent_info.to_dict(),
                timeout=10,
            )
            if resp.status_code in (200, 201):
                self._registered = True
                logger.info("Agent registered: %s (%s)", self.label, self.agent_id)
                return True, "registered"
            elif resp.status_code == 409:
                # Already registered, update instead
                return self.update_registration()
            else:
                msg = f"Registration failed: HTTP {resp.status_code}"
                logger.warning(msg)
                return False, msg
        except Exception as e:
            msg = f"Registration error: {e}"
            logger.error(msg)
            return False, msg

    def update_registration(self) -> tuple[bool, str]:
        """Update existing registration."""
        if not self._agent_info:
            self._agent_info = self.build_agent_info()

        self._agent_info.last_heartbeat = time.time()

        if not HAS_REQUESTS:
            return False, "requests library not installed"

        try:
            resp = _requests.put(
                f"{self.api_url}/api/agents/{self.agent_id}",
                headers=self._headers(),
                json=self._agent_info.to_dict(),
                timeout=10,
            )
            if resp.status_code in (200, 201, 204):
                self._registered = True
                return True, "updated"
            return False, f"Update failed: HTTP {resp.status_code}"
        except Exception as e:
            return False, f"Update error: {e}"

    def heartbeat(self) -> tuple[bool, str]:
        """Send heartbeat to keep registration alive."""
        if not HAS_REQUESTS:
            return False, "requests not installed"

        payload = {
            "agent_id": self.agent_id,
            "label": self.label,
            "status": self._agent_info.status if self._agent_info else "online",
            "timestamp": time.time(),
        }

        try:
            resp = _requests.post(
                f"{self.api_url}/api/agents/{self.agent_id}/heartbeat",
                headers=self._headers(),
                json=payload,
                timeout=5,
            )
            if self._agent_info:
                self._agent_info.last_heartbeat = time.time()
            return resp.status_code < 400, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    def deregister(self) -> tuple[bool, str]:
        """Deregister this agent."""
        if not HAS_REQUESTS:
            return False, "requests not installed"

        try:
            resp = _requests.delete(
                f"{self.api_url}/api/agents/{self.agent_id}",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code in (200, 204, 404):
                self._registered = False
                logger.info("Agent deregistered: %s", self.agent_id)
                return True, "deregistered"
            return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    def set_status(self, status: str) -> tuple[bool, str]:
        """Update agent status (online/offline/busy/error)."""
        if self._agent_info:
            self._agent_info.status = status
        return self.heartbeat()

    @staticmethod
    def list_agents(api_url: str, api_key: str) -> tuple[bool, list[dict]]:
        """List all registered agents from the API."""
        if not HAS_REQUESTS:
            return False, []

        try:
            resp = _requests.get(
                f"{api_url.rstrip('/')}/api/agents",
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, resp.json()
            return False, []
        except Exception:
            return False, []
