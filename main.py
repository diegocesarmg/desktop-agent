"""GCC Desktop Agent — Main entry point."""

import json
import logging
import sys
import os

# Fix console encoding for emoji/unicode on Windows (cp1252 systems)
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import AppConfig
from wizard import run_wizard
from tray import start_tray, BridgeWebSocket
from shell_executor import ShellExecutor, ShellType
from desktop_control import DesktopController
from mission_manager import MissionManager
from multi_agent import MultiAgentRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("gcc.main")


class GCCDesktopAgent:
    """Orchestrates all components of the desktop agent."""

    def __init__(self):
        self.config = AppConfig.load()
        self.shell = ShellExecutor(self.config)
        self.desktop = DesktopController()
        self.missions: MissionManager = None  # type: ignore
        self.registry: MultiAgentRegistry = None  # type: ignore
        self.bridge: BridgeWebSocket = None  # type: ignore

    def _ensure_configured(self):
        if not self.config.is_configured:
            logger.info("No configuration found — launching setup wizard")
            self.config = run_wizard(self.config)
            if not self.config.is_configured:
                logger.error("Setup incomplete — exiting")
                sys.exit(1)

    def _setup_components(self):
        os.makedirs(self.config.storage.data_dir, exist_ok=True)
        self.missions = MissionManager(self.config)
        self.registry = MultiAgentRegistry(self.config)
        self.registry.register()

    def _handle_ws_message(self, data: dict):
        """Route incoming WebSocket messages to appropriate handlers."""
        msg_type = data.get("type", "")
        logger.debug("WS message: %s", msg_type)

        handlers = {
            "execute_command": self._handle_execute,
            "desktop_action": self._handle_desktop_action,
            "mission_create": self._handle_mission_create,
            "mission_update": self._handle_mission_update,
            "mission_list": self._handle_mission_list,
            "mission_delete": self._handle_mission_delete,
            "ping": self._handle_ping,
            "sync": self._handle_sync,
        }
        handler = handlers.get(msg_type)
        if handler:
            try:
                result = handler(data)
                if result and self.bridge:
                    self.bridge.send_sync(result)
            except Exception as e:
                logger.error("Handler error for %s: %s", msg_type, e)
                if self.bridge:
                    self.bridge.send_sync({
                        "type": "error", "request_type": msg_type,
                        "error": str(e), "request_id": data.get("request_id"),
                    })

    def _handle_execute(self, data: dict) -> dict:
        command = data.get("command", "")
        shell = ShellType(data.get("shell", "powershell"))
        mission_id = data.get("mission_id")

        # Check mission-level yolo override
        mission = self.missions.get(mission_id) if mission_id else None
        if mission and mission.yolo_mode:
            self.shell.sandbox.mode = "yolo"

        # Stream output via WS
        if self.bridge:
            self.shell.set_output_handler(self.shell.create_ws_output_handler(self.bridge, mission_id))

        result = self.shell.execute(command, shell, mission_id)

        # Record in mission
        if mission_id:
            self.missions.record_command(mission_id, command, shell.value,
                                         result.exit_code, result.duration_ms, result.stdout)

        return {
            "type": "command_result",
            "request_id": data.get("request_id"),
            "command": command,
            "exit_code": result.exit_code,
            "stdout": result.stdout[-5000:],  # Last 5k chars
            "stderr": result.stderr[-2000:],
            "duration_ms": result.duration_ms,
            "blocked": result.blocked,
            "mission_id": mission_id,
        }

    def _handle_desktop_action(self, data: dict) -> dict:
        action = data.get("action", "")
        params = data.get("params", {})
        mission_id = data.get("mission_id")

        mission = self.missions.get(mission_id) if mission_id else None
        permissions = mission.desktop_permissions if mission else DesktopController and \
            __import__("desktop_control").DesktopPermissions.screenshot_only()

        result = self.desktop.execute_action(permissions, action, params)
        result["type"] = "desktop_result"
        result["request_id"] = data.get("request_id")
        result["mission_id"] = mission_id
        return result

    def _handle_mission_create(self, data: dict) -> dict:
        mission = self.missions.create(
            name=data.get("name", "Untitled"),
            description=data.get("description", ""),
            yolo_mode=data.get("yolo_mode", False),
            desktop_actions=data.get("desktop_actions"),
        )
        return {"type": "mission_created", "mission": mission.to_dict(), "request_id": data.get("request_id")}

    def _handle_mission_update(self, data: dict) -> dict:
        mid = data.get("mission_id", "")
        updates = {k: v for k, v in data.items() if k not in ("type", "mission_id", "request_id")}
        mission = self.missions.update(mid, **updates)
        if mission:
            return {"type": "mission_updated", "mission": mission.to_dict(), "request_id": data.get("request_id")}
        return {"type": "error", "error": "Mission not found", "request_id": data.get("request_id")}

    def _handle_mission_list(self, data: dict) -> dict:
        missions = self.missions.list_all()
        return {
            "type": "mission_list",
            "missions": [m.to_dict() for m in missions],
            "request_id": data.get("request_id"),
        }

    def _handle_mission_delete(self, data: dict) -> dict:
        ok = self.missions.delete(data.get("mission_id", ""))
        return {"type": "mission_deleted", "success": ok, "request_id": data.get("request_id")}

    def _handle_ping(self, data: dict) -> dict:
        return {"type": "pong", "request_id": data.get("request_id"), "agent_id": self.config.identity.agent_id}

    def _handle_sync(self, data: dict) -> dict:
        self.missions.sync_from_backend()
        active = len(self.missions.list_all(status=__import__("mission_manager").MissionStatus.ACTIVE))
        return {
            "type": "sync_complete",
            "missions_count": len(self.missions.missions),
            "active_missions": active,
            "request_id": data.get("request_id"),
        }

    def run(self):
        """Main entry point."""
        logger.info("GCC Desktop Agent starting…")
        self._ensure_configured()
        self._setup_components()

        tray, self.bridge = start_tray(self.config, on_message=self._handle_ws_message)
        logger.info("Agent ready — %s (%s)", self.config.identity.label, self.config.identity.agent_id)
        tray.run()  # Blocks until quit


def main():
    if "--wizard" in sys.argv:
        cfg = AppConfig.load()
        run_wizard(cfg)
        return

    agent = GCCDesktopAgent()
    agent.run()


if __name__ == "__main__":
    main()
