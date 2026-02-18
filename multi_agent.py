"""Multi-desktop agent registration — unique labels, heartbeat, capability reporting."""

import json
import logging
import platform
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests

from config import AppConfig

logger = logging.getLogger("gcc.multi")


@dataclass
class AgentStatus:
    agent_id: str
    label: str
    hostname: str
    platform: str
    python_version: str
    capabilities: list[str]
    status: str = "online"
    uptime_seconds: float = 0
    missions_active: int = 0
    missions_total: int = 0
    last_heartbeat: float = 0
    version: str = "1.0.0"

    def to_dict(self) -> dict:
        return asdict(self)


class MultiAgentRegistry:
    """Handles agent registration, heartbeat, and capability reporting to the backend."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._start_time = time.time()
        self._registered = False

    def _api_url(self, path: str) -> str:
        base = self.config.connection.dashboard_url.rstrip("/")
        return f"{base}/api{path}"

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.config.connection.api_key,
            "Content-Type": "application/json",
        }

    def build_status(self, missions_active: int = 0, missions_total: int = 0) -> AgentStatus:
        return AgentStatus(
            agent_id=self.config.identity.agent_id,
            label=self.config.identity.label,
            hostname=platform.node(),
            platform=f"{platform.system()} {platform.release()}",
            python_version=platform.python_version(),
            capabilities=self.config.identity.capabilities,
            status="online",
            uptime_seconds=time.time() - self._start_time,
            missions_active=missions_active,
            missions_total=missions_total,
            last_heartbeat=time.time(),
        )

    def register(self) -> bool:
        """Register this agent with the backend. Returns True on success."""
        status = self.build_status()
        try:
            resp = requests.post(
                self._api_url("/agents/register"),
                json=status.to_dict(),
                headers=self._headers(),
                timeout=10,
            )
            if resp.ok:
                self._registered = True
                logger.info("Agent registered: %s (%s)", status.label, status.agent_id)
                return True
            logger.warning("Registration failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.debug("Registration error (non-critical): %s", e)
        return False

    def send_heartbeat(self, missions_active: int = 0, missions_total: int = 0) -> bool:
        """Send heartbeat to backend."""
        status = self.build_status(missions_active, missions_total)
        try:
            resp = requests.post(
                self._api_url(f"/agents/{self.config.identity.agent_id}/heartbeat"),
                json=status.to_dict(),
                headers=self._headers(),
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def unregister(self) -> bool:
        """Mark agent as offline."""
        try:
            resp = requests.post(
                self._api_url(f"/agents/{self.config.identity.agent_id}/offline"),
                json={"agent_id": self.config.identity.agent_id, "timestamp": time.time()},
                headers=self._headers(),
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def build_ws_heartbeat(self, missions_active: int = 0, missions_total: int = 0) -> dict:
        """Build heartbeat payload for WebSocket."""
        status = self.build_status(missions_active, missions_total)
        return {
            "type": "heartbeat",
            **status.to_dict(),
        }

    def build_ws_registration(self) -> dict:
        """Build registration payload for WebSocket."""
        status = self.build_status()
        return {
            "type": "register",
            **status.to_dict(),
        }
