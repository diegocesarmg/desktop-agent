"""GCC Desktop Agent — Desktop Control Module.

Task #4: Screenshot, click, type, hotkeys, window management via pyautogui.
Per-mission permission checks.
"""

import base64
import enum
import io
import logging
import platform
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("gcc-agent.desktop")

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False
    pyautogui = None


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class DesktopAction(enum.Enum):
    SCREENSHOT = "screenshot"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE_TEXT = "type_text"
    HOTKEY = "hotkey"
    MOVE_MOUSE = "move_mouse"
    SCROLL = "scroll"
    DRAG = "drag"
    GET_MOUSE_POSITION = "get_mouse_position"
    GET_SCREEN_SIZE = "get_screen_size"
    LOCATE_ON_SCREEN = "locate_on_screen"
    GET_ACTIVE_WINDOW = "get_active_window"
    LIST_WINDOWS = "list_windows"
    FOCUS_WINDOW = "focus_window"
    MINIMIZE_WINDOW = "minimize_window"
    MAXIMIZE_WINDOW = "maximize_window"
    CLOSE_WINDOW = "close_window"
    RESIZE_WINDOW = "resize_window"
    MOVE_WINDOW = "move_window"


# Actions that are read-only / non-destructive
SAFE_ACTIONS = {
    DesktopAction.SCREENSHOT,
    DesktopAction.GET_MOUSE_POSITION,
    DesktopAction.GET_SCREEN_SIZE,
    DesktopAction.GET_ACTIVE_WINDOW,
    DesktopAction.LIST_WINDOWS,
}


@dataclass
class DesktopRequest:
    id: str
    action: DesktopAction
    mission_id: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    keys: Optional[list[str]] = None
    duration: float = 0.0
    # Screenshot options
    region: Optional[tuple[int, int, int, int]] = None  # x, y, width, height
    image_format: str = "png"
    # Window management
    window_title: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # Scroll
    clicks: int = 3
    # Drag
    end_x: Optional[int] = None
    end_y: Optional[int] = None
    # Type options
    interval: float = 0.02


@dataclass
class DesktopResult:
    request_id: str
    action: str
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    duration_ms: int = 0
    mission_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Mission Permission Check
# ---------------------------------------------------------------------------

class DesktopPermissionManager:
    """Per-mission permission control for desktop actions."""

    def __init__(self):
        self._permissions: dict[str, dict] = {}

    def set_permissions(self, mission_id: str, permissions: dict):
        """Set desktop permissions for a mission.

        permissions can contain:
          - allowed_actions: list of action names allowed
          - blocked_actions: list of action names blocked
          - allow_input: bool — allow click/type/hotkey (default True)
          - allow_window_mgmt: bool — allow window operations (default True)
          - safe_only: bool — only allow read-only actions (default False)
        """
        self._permissions[mission_id] = permissions

    def remove_permissions(self, mission_id: str):
        self._permissions.pop(mission_id, None)

    def check_permission(self, mission_id: Optional[str], action: DesktopAction) -> tuple[bool, str]:
        if not mission_id:
            return True, "no mission context"

        perms = self._permissions.get(mission_id)
        if not perms:
            return True, "no restrictions"

        # Safe-only mode
        if perms.get("safe_only", False) and action not in SAFE_ACTIONS:
            return False, f"mission is safe_only, {action.value} not allowed"

        # Input control
        input_actions = {DesktopAction.CLICK, DesktopAction.DOUBLE_CLICK, DesktopAction.RIGHT_CLICK,
                         DesktopAction.TYPE_TEXT, DesktopAction.HOTKEY, DesktopAction.MOVE_MOUSE,
                         DesktopAction.SCROLL, DesktopAction.DRAG}
        if not perms.get("allow_input", True) and action in input_actions:
            return False, "input actions not allowed for mission"

        # Window management control
        window_actions = {DesktopAction.FOCUS_WINDOW, DesktopAction.MINIMIZE_WINDOW,
                          DesktopAction.MAXIMIZE_WINDOW, DesktopAction.CLOSE_WINDOW,
                          DesktopAction.RESIZE_WINDOW, DesktopAction.MOVE_WINDOW}
        if not perms.get("allow_window_mgmt", True) and action in window_actions:
            return False, "window management not allowed for mission"

        # Explicit allowed/blocked lists
        blocked = perms.get("blocked_actions", [])
        if action.value in blocked:
            return False, f"{action.value} is blocked for mission"

        allowed = perms.get("allowed_actions")
        if allowed is not None and action.value not in allowed:
            return False, f"{action.value} not in allowed list"

        return True, "allowed"


# ---------------------------------------------------------------------------
# Desktop Controller
# ---------------------------------------------------------------------------

class DesktopController:
    """Desktop automation controller wrapping pyautogui with permission checks."""

    def __init__(self, permission_manager: Optional[DesktopPermissionManager] = None):
        self.permission_manager = permission_manager or DesktopPermissionManager()
        if not HAS_PYAUTOGUI:
            logger.warning("pyautogui not installed — desktop control unavailable")

    @property
    def available(self) -> bool:
        return HAS_PYAUTOGUI

    def execute(self, request: DesktopRequest) -> DesktopResult:
        """Execute a desktop action with permission checking."""
        start = time.monotonic()

        if not HAS_PYAUTOGUI:
            return DesktopResult(
                request_id=request.id, action=request.action.value,
                success=False, error="pyautogui not installed",
            )

        # Permission check
        allowed, reason = self.permission_manager.check_permission(request.mission_id, request.action)
        if not allowed:
            return DesktopResult(
                request_id=request.id, action=request.action.value,
                success=False, error=f"Permission denied: {reason}",
                mission_id=request.mission_id,
            )

        try:
            data = self._dispatch(request)
            duration = int((time.monotonic() - start) * 1000)
            return DesktopResult(
                request_id=request.id, action=request.action.value,
                success=True, data=data, duration_ms=duration,
                mission_id=request.mission_id,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error("Desktop action %s failed: %s", request.action.value, e)
            return DesktopResult(
                request_id=request.id, action=request.action.value,
                success=False, error=str(e), duration_ms=duration,
                mission_id=request.mission_id,
            )

    def _dispatch(self, req: DesktopRequest) -> Optional[dict]:
        action = req.action

        if action == DesktopAction.SCREENSHOT:
            return self._screenshot(req)
        elif action == DesktopAction.CLICK:
            pyautogui.click(x=req.x, y=req.y)
            return {"x": req.x, "y": req.y}
        elif action == DesktopAction.DOUBLE_CLICK:
            pyautogui.doubleClick(x=req.x, y=req.y)
            return {"x": req.x, "y": req.y}
        elif action == DesktopAction.RIGHT_CLICK:
            pyautogui.rightClick(x=req.x, y=req.y)
            return {"x": req.x, "y": req.y}
        elif action == DesktopAction.TYPE_TEXT:
            if req.text:
                pyautogui.typewrite(req.text, interval=req.interval)
            return {"typed": len(req.text or "")}
        elif action == DesktopAction.HOTKEY:
            if req.keys:
                pyautogui.hotkey(*req.keys)
            return {"keys": req.keys}
        elif action == DesktopAction.MOVE_MOUSE:
            pyautogui.moveTo(x=req.x, y=req.y, duration=req.duration)
            return {"x": req.x, "y": req.y}
        elif action == DesktopAction.SCROLL:
            pyautogui.scroll(req.clicks, x=req.x, y=req.y)
            return {"clicks": req.clicks}
        elif action == DesktopAction.DRAG:
            pyautogui.moveTo(req.x, req.y)
            pyautogui.drag(
                (req.end_x or 0) - (req.x or 0),
                (req.end_y or 0) - (req.y or 0),
                duration=req.duration or 0.5,
            )
            return {"from": [req.x, req.y], "to": [req.end_x, req.end_y]}
        elif action == DesktopAction.GET_MOUSE_POSITION:
            pos = pyautogui.position()
            return {"x": pos.x, "y": pos.y}
        elif action == DesktopAction.GET_SCREEN_SIZE:
            size = pyautogui.size()
            return {"width": size.width, "height": size.height}
        elif action == DesktopAction.GET_ACTIVE_WINDOW:
            return self._get_active_window()
        elif action == DesktopAction.LIST_WINDOWS:
            return self._list_windows()
        elif action == DesktopAction.FOCUS_WINDOW:
            return self._focus_window(req.window_title)
        elif action == DesktopAction.MINIMIZE_WINDOW:
            return self._window_op(req.window_title, "minimize")
        elif action == DesktopAction.MAXIMIZE_WINDOW:
            return self._window_op(req.window_title, "maximize")
        elif action == DesktopAction.CLOSE_WINDOW:
            return self._window_op(req.window_title, "close")
        elif action == DesktopAction.RESIZE_WINDOW:
            return self._resize_window(req.window_title, req.width, req.height)
        elif action == DesktopAction.MOVE_WINDOW:
            return self._move_window(req.window_title, req.x, req.y)

        return None

    def _screenshot(self, req: DesktopRequest) -> dict:
        img = pyautogui.screenshot(region=req.region)
        buf = io.BytesIO()
        img.save(buf, format=req.image_format.upper())
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {
            "image_base64": b64,
            "format": req.image_format,
            "width": img.width,
            "height": img.height,
        }

    def _get_active_window(self) -> dict:
        try:
            win = pyautogui.getActiveWindow()
            if win:
                return {
                    "title": win.title,
                    "left": win.left, "top": win.top,
                    "width": win.width, "height": win.height,
                }
        except Exception:
            pass
        return {"title": None}

    def _list_windows(self) -> dict:
        try:
            windows = pyautogui.getAllWindows()
            return {
                "windows": [
                    {"title": w.title, "left": w.left, "top": w.top,
                     "width": w.width, "height": w.height}
                    for w in windows if w.title
                ]
            }
        except Exception:
            return {"windows": [], "note": "window listing not supported on this platform"}

    def _focus_window(self, title: Optional[str]) -> dict:
        if not title:
            return {"error": "window_title required"}
        try:
            wins = pyautogui.getWindowsWithTitle(title)
            if wins:
                wins[0].activate()
                return {"focused": title}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"window '{title}' not found"}

    def _window_op(self, title: Optional[str], op: str) -> dict:
        if not title:
            return {"error": "window_title required"}
        try:
            wins = pyautogui.getWindowsWithTitle(title)
            if wins:
                getattr(wins[0], op)()
                return {op: title}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"window '{title}' not found"}

    def _resize_window(self, title: Optional[str], width: Optional[int], height: Optional[int]) -> dict:
        if not title:
            return {"error": "window_title required"}
        try:
            wins = pyautogui.getWindowsWithTitle(title)
            if wins:
                wins[0].resizeTo(width or wins[0].width, height or wins[0].height)
                return {"resized": title, "width": width, "height": height}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"window '{title}' not found"}

    def _move_window(self, title: Optional[str], x: Optional[int], y: Optional[int]) -> dict:
        if not title:
            return {"error": "window_title required"}
        try:
            wins = pyautogui.getWindowsWithTitle(title)
            if wins:
                wins[0].moveTo(x or 0, y or 0)
                return {"moved": title, "x": x, "y": y}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"window '{title}' not found"}


def desktop_result_to_dict(result: DesktopResult) -> dict:
    return {
        "request_id": result.request_id,
        "action": result.action,
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "mission_id": result.mission_id,
    }
