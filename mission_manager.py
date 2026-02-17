"""GCC Desktop Agent — Mission Manager.

Task #5: CRUD for missions, YOLO mode, command tracking per mission,
sync with backend API.
"""

import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("gcc-agent.missions")

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    _requests = None
    HAS_REQUESTS = False


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class MissionStatus(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionMode(enum.Enum):
    ASSISTED = "assisted"
    YOLO = "yolo"
    WHITELIST = "whitelist"


@dataclass
class CommandRecord:
    """Record of a command executed within a mission."""
    id: str
    command: str
    status: str
    exit_code: Optional[int] = None
    shell_type: str = ""
    stdout_preview: str = ""  # first 500 chars
    stderr_preview: str = ""
    duration_ms: int = 0
    timestamp: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "shell_type": self.shell_type,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            "error": self.error,
        }


@dataclass
class Mission:
    """A mission groups related commands with shared permissions and tracking."""
    id: str
    name: str
    description: str = ""
    status: MissionStatus = MissionStatus.PENDING
    execution_mode: ExecutionMode = ExecutionMode.ASSISTED
    yolo: bool = False  # Shortcut: if True, execution_mode = YOLO
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: Optional[float] = None
    commands: list[CommandRecord] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # Permission overrides
    allowed_commands: Optional[list[str]] = None
    blocked_commands: Optional[list[str]] = None
    max_timeout: int = 300
    # Desktop permissions
    allow_desktop_input: bool = True
    allow_window_mgmt: bool = True
    desktop_safe_only: bool = False

    def __post_init__(self):
        if self.yolo:
            self.execution_mode = ExecutionMode.YOLO
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "execution_mode": self.execution_mode.value,
            "yolo": self.yolo,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "commands": [c.to_dict() for c in self.commands],
            "command_count": len(self.commands),
            "metadata": self.metadata,
            "allowed_commands": self.allowed_commands,
            "blocked_commands": self.blocked_commands,
            "max_timeout": self.max_timeout,
            "allow_desktop_input": self.allow_desktop_input,
            "allow_window_mgmt": self.allow_window_mgmt,
            "desktop_safe_only": self.desktop_safe_only,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Mission":
        commands = [CommandRecord(**c) for c in data.get("commands", [])]
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            status=MissionStatus(data.get("status", "pending")),
            execution_mode=ExecutionMode(data.get("execution_mode", "assisted")),
            yolo=data.get("yolo", False),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            completed_at=data.get("completed_at"),
            commands=commands,
            metadata=data.get("metadata", {}),
            allowed_commands=data.get("allowed_commands"),
            blocked_commands=data.get("blocked_commands"),
            max_timeout=data.get("max_timeout", 300),
            allow_desktop_input=data.get("allow_desktop_input", True),
            allow_window_mgmt=data.get("allow_window_mgmt", True),
            desktop_safe_only=data.get("desktop_safe_only", False),
        )


# ---------------------------------------------------------------------------
# Mission Manager
# ---------------------------------------------------------------------------

class MissionManager:
    """CRUD manager for missions with backend API sync."""

    def __init__(self, api_url: str = "", api_key: str = "", storage_path: Optional[str] = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._missions: dict[str, Mission] = {}
        self._storage_path = Path(storage_path) if storage_path else None
        if self._storage_path:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    # -- CRUD ---------------------------------------------------------------

    def create(self, name: str, description: str = "", yolo: bool = False, **kwargs) -> Mission:
        mission = Mission(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            yolo=yolo,
            **kwargs,
        )
        self._missions[mission.id] = mission
        self._save_to_disk()
        self._sync_to_backend(mission, "create")
        logger.info("Mission created: %s (%s) yolo=%s", mission.name, mission.id, mission.yolo)
        return mission

    def get(self, mission_id: str) -> Optional[Mission]:
        return self._missions.get(mission_id)

    def list_missions(self, status: Optional[MissionStatus] = None) -> list[Mission]:
        missions = list(self._missions.values())
        if status:
            missions = [m for m in missions if m.status == status]
        return sorted(missions, key=lambda m: m.updated_at, reverse=True)

    def update(self, mission_id: str, **kwargs) -> Optional[Mission]:
        mission = self._missions.get(mission_id)
        if not mission:
            return None

        for key, value in kwargs.items():
            if key == "status" and isinstance(value, str):
                value = MissionStatus(value)
            if key == "execution_mode" and isinstance(value, str):
                value = ExecutionMode(value)
            if hasattr(mission, key):
                setattr(mission, key, value)

        if kwargs.get("yolo"):
            mission.execution_mode = ExecutionMode.YOLO

        mission.updated_at = time.time()
        self._save_to_disk()
        self._sync_to_backend(mission, "update")
        return mission

    def delete(self, mission_id: str) -> bool:
        if mission_id in self._missions:
            mission = self._missions.pop(mission_id)
            self._save_to_disk()
            self._sync_to_backend(mission, "delete")
            logger.info("Mission deleted: %s", mission_id)
            return True
        return False

    def complete(self, mission_id: str) -> Optional[Mission]:
        return self.update(mission_id, status=MissionStatus.COMPLETED, completed_at=time.time())

    def fail(self, mission_id: str, error: str = "") -> Optional[Mission]:
        return self.update(mission_id, status=MissionStatus.FAILED, metadata={"error": error})

    def activate(self, mission_id: str) -> Optional[Mission]:
        return self.update(mission_id, status=MissionStatus.ACTIVE)

    # -- YOLO mode ----------------------------------------------------------

    def set_yolo(self, mission_id: str, enabled: bool = True) -> Optional[Mission]:
        return self.update(mission_id, yolo=enabled)

    # -- Command tracking ---------------------------------------------------

    def track_command(self, mission_id: str, command_record: CommandRecord) -> bool:
        mission = self._missions.get(mission_id)
        if not mission:
            return False
        mission.commands.append(command_record)
        mission.updated_at = time.time()
        self._save_to_disk()
        return True

    def get_command_history(self, mission_id: str, limit: int = 50) -> list[CommandRecord]:
        mission = self._missions.get(mission_id)
        if not mission:
            return []
        return mission.commands[-limit:]

    def get_mission_stats(self, mission_id: str) -> dict:
        mission = self._missions.get(mission_id)
        if not mission:
            return {}
        total = len(mission.commands)
        completed = sum(1 for c in mission.commands if c.status == "completed")
        failed = sum(1 for c in mission.commands if c.status == "failed")
        total_duration = sum(c.duration_ms for c in mission.commands)
        return {
            "mission_id": mission_id,
            "total_commands": total,
            "completed": completed,
            "failed": failed,
            "other": total - completed - failed,
            "total_duration_ms": total_duration,
            "avg_duration_ms": total_duration // total if total else 0,
        }

    # -- Persistence --------------------------------------------------------

    def _save_to_disk(self):
        if not self._storage_path:
            return
        try:
            data = {mid: m.to_dict() for mid, m in self._missions.items()}
            path = self._storage_path / "missions.json"
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save missions: %s", e)

    def _load_from_disk(self):
        if not self._storage_path:
            return
        path = self._storage_path / "missions.json"
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for mid, mdata in data.items():
                self._missions[mid] = Mission.from_dict(mdata)
            logger.info("Loaded %d missions from disk", len(self._missions))
        except Exception as e:
            logger.error("Failed to load missions: %s", e)

    # -- Backend API sync ---------------------------------------------------

    def _sync_to_backend(self, mission: Mission, action: str):
        if not self.api_url or not self.api_key or not HAS_REQUESTS:
            return
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        try:
            if action == "create":
                _requests.post(
                    f"{self.api_url}/api/missions",
                    headers=headers, json=mission.to_dict(), timeout=5,
                )
            elif action == "update":
                _requests.put(
                    f"{self.api_url}/api/missions/{mission.id}",
                    headers=headers, json=mission.to_dict(), timeout=5,
                )
            elif action == "delete":
                _requests.delete(
                    f"{self.api_url}/api/missions/{mission.id}",
                    headers=headers, timeout=5,
                )
            logger.debug("Synced mission %s (%s) to backend", mission.id, action)
        except Exception as e:
            logger.warning("Backend sync failed for mission %s: %s", mission.id, e)

    def sync_from_backend(self) -> int:
        """Pull missions from backend API. Returns count of synced missions."""
        if not self.api_url or not self.api_key or not HAS_REQUESTS:
            return 0
        headers = {"X-API-Key": self.api_key}
        try:
            resp = _requests.get(f"{self.api_url}/api/missions", headers=headers, timeout=10)
            if resp.status_code == 200:
                missions_data = resp.json()
                if isinstance(missions_data, list):
                    for mdata in missions_data:
                        mid = mdata.get("id")
                        if mid:
                            self._missions[mid] = Mission.from_dict(mdata)
                    self._save_to_disk()
                    return len(missions_data)
        except Exception as e:
            logger.warning("Failed to sync from backend: %s", e)
        return 0

    # -- Permission helpers (bridge to shell_executor/desktop_control) ------

    def get_shell_permissions(self, mission_id: str) -> dict:
        """Get shell executor permissions for a mission."""
        mission = self._missions.get(mission_id)
        if not mission:
            return {}
        perms = {"execution_mode": mission.execution_mode.value, "max_timeout": mission.max_timeout}
        if mission.allowed_commands is not None:
            perms["allowed_commands"] = mission.allowed_commands
        if mission.blocked_commands is not None:
            perms["blocked_commands"] = mission.blocked_commands
        return perms

    def get_desktop_permissions(self, mission_id: str) -> dict:
        """Get desktop control permissions for a mission."""
        mission = self._missions.get(mission_id)
        if not mission:
            return {}
        return {
            "allow_input": mission.allow_desktop_input,
            "allow_window_mgmt": mission.allow_window_mgmt,
            "safe_only": mission.desktop_safe_only,
        }
