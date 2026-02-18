"""Desktop Agent — Local HTTP Server (Task #103).

Exposes mouse and keyboard control endpoints on localhost (default: 7070).
The GCC API relays requests here via POST /api/agents/{label}/mouse and /keyboard.

Permission model:
  - yolo     → execute immediately, no confirmation
  - assisted → show tkinter dialog asking the user before each action
"""

from __future__ import annotations

import base64
import io
import logging
import threading
import time
import uuid
from typing import Any, Optional

from config import load_config

logger = logging.getLogger("gcc.local_server")

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("fastapi/uvicorn not installed — local HTTP server unavailable")

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False
    logger.warning("pyautogui not installed — mouse/keyboard control unavailable")


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _get_permission_mode() -> str:
    """Read permission_mode from agent config (default: assisted)."""
    cfg = load_config()
    return cfg.get("permission_mode", "assisted")


def _confirm_action(action_type: str, params: dict) -> bool:
    """Show a tkinter confirmation dialog. Returns True if approved."""
    mode = _get_permission_mode()
    if mode == "yolo":
        return True

    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        detail = "\n".join(f"  {k}: {v}" for k, v in params.items())
        ok = messagebox.askyesno(
            "GCC Agent — Aprovar Ação Desktop?",
            f"Ação:  {action_type}\nParâmetros:\n{detail}\n\nPermitir?",
            parent=root,
        )
        root.destroy()
        return bool(ok)
    except Exception as e:
        logger.error("Confirmation dialog error: %s", e)
        return False


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def _take_screenshot() -> Optional[str]:
    """Return a base64-encoded PNG screenshot, or None on failure."""
    if not HAS_PYAUTOGUI:
        return None
    try:
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning("Screenshot failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# FastAPI app (created lazily)
# ---------------------------------------------------------------------------

def create_app() -> "FastAPI":  # noqa: F821
    if not HAS_FASTAPI:
        raise ImportError("fastapi is not installed")

    app = FastAPI(
        title="GCC Desktop Agent — Local API",
        description="Mouse & keyboard control relay. Auth via agent API key.",
        version="1.0.0",
    )

    # ------------------------------------------------------------------ auth

    async def _verify_key(x_api_key: Optional[str] = Header(None)) -> None:
        cfg = load_config()
        expected = cfg.get("api_key", "")
        if not expected:
            return  # No key configured → open (local-only anyway)
        if x_api_key != expected:
            raise HTTPException(status_code=403, detail="Invalid API key")

    # ---------------------------------------------------------------- schemas

    class MouseRequest(BaseModel):
        action: str                         # mouse_move | mouse_click | mouse_double_click
        x: Optional[int] = None
        y: Optional[int] = None
        button: str = "left"                # left | right | middle
        duration: float = 0.25             # movement duration (seconds)
        screenshot_before: bool = False
        screenshot_after: bool = True
        mission_id: Optional[str] = None

    class KeyboardRequest(BaseModel):
        action: str                         # key_press | type_text | hotkey
        key: Optional[str] = None           # single key name (for key_press)
        text: Optional[str] = None          # text to type (for type_text)
        keys: Optional[list[str]] = None    # key combo (for hotkey, e.g. ["ctrl","c"])
        interval: float = 0.03             # typing interval (seconds)
        screenshot_before: bool = False
        screenshot_after: bool = True
        mission_id: Optional[str] = None

    # --------------------------------------------------------------- /health

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "pyautogui": HAS_PYAUTOGUI,
            "permission_mode": _get_permission_mode(),
        }

    # --------------------------------------------------------------- /api/mouse

    @app.post("/api/mouse")
    async def mouse_action(req: MouseRequest) -> dict:
        """Execute a mouse action (move, click, double-click)."""
        if not HAS_PYAUTOGUI:
            raise HTTPException(503, "pyautogui not installed")

        request_id = str(uuid.uuid4())[:8]
        params: dict[str, Any] = {"action": req.action}
        if req.x is not None:
            params["x"] = req.x
        if req.y is not None:
            params["y"] = req.y
        if req.action != "mouse_move":
            params["button"] = req.button

        # Confirmation
        if not _confirm_action(req.action, params):
            return {
                "request_id": request_id,
                "action": req.action,
                "approved": False,
                "success": False,
                "error": "Action denied by user",
            }

        # Screenshot before
        before_b64: Optional[str] = None
        if req.screenshot_before:
            before_b64 = _take_screenshot()

        # Execute
        t0 = time.monotonic()
        try:
            if req.action == "mouse_move":
                if req.x is None or req.y is None:
                    raise ValueError("x and y are required for mouse_move")
                pyautogui.moveTo(req.x, req.y, duration=req.duration)

            elif req.action == "mouse_click":
                if req.x is None or req.y is None:
                    raise ValueError("x and y are required for mouse_click")
                pyautogui.click(req.x, req.y, button=req.button)

            elif req.action == "mouse_double_click":
                if req.x is None or req.y is None:
                    raise ValueError("x and y are required for mouse_double_click")
                pyautogui.doubleClick(req.x, req.y, button=req.button)

            else:
                raise ValueError(f"Unknown mouse action: {req.action!r}")

            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.info("[Mouse] %s at (%s,%s) — %dms", req.action, req.x, req.y, duration_ms)

            # Screenshot after
            after_b64: Optional[str] = None
            if req.screenshot_after:
                time.sleep(0.15)  # Allow UI to react
                after_b64 = _take_screenshot()

            return {
                "request_id": request_id,
                "action": req.action,
                "approved": True,
                "success": True,
                "x": req.x,
                "y": req.y,
                "button": req.button,
                "duration_ms": duration_ms,
                "screenshot_before": before_b64,
                "screenshot_after": after_b64,
            }

        except Exception as e:
            logger.error("[Mouse] %s failed: %s", req.action, e)
            return {
                "request_id": request_id,
                "action": req.action,
                "approved": True,
                "success": False,
                "error": str(e),
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }

    # --------------------------------------------------------------- /api/keyboard

    @app.post("/api/keyboard")
    async def keyboard_action(req: KeyboardRequest) -> dict:
        """Execute a keyboard action (key_press, type_text, hotkey)."""
        if not HAS_PYAUTOGUI:
            raise HTTPException(503, "pyautogui not installed")

        request_id = str(uuid.uuid4())[:8]
        params: dict[str, Any] = {"action": req.action}
        if req.key:
            params["key"] = req.key
        if req.text:
            params["text"] = req.text[:50] + "…" if len(req.text) > 50 else req.text
        if req.keys:
            params["keys"] = req.keys

        # Confirmation
        if not _confirm_action(req.action, params):
            return {
                "request_id": request_id,
                "action": req.action,
                "approved": False,
                "success": False,
                "error": "Action denied by user",
            }

        # Screenshot before
        before_b64: Optional[str] = None
        if req.screenshot_before:
            before_b64 = _take_screenshot()

        # Execute
        t0 = time.monotonic()
        try:
            if req.action == "key_press":
                if not req.key:
                    raise ValueError("key is required for key_press")
                pyautogui.press(req.key)
                logger.info("[Keyboard] key_press: %s", req.key)

            elif req.action == "type_text":
                if not req.text:
                    raise ValueError("text is required for type_text")
                pyautogui.typewrite(req.text, interval=req.interval)
                logger.info("[Keyboard] type_text: %d chars", len(req.text))

            elif req.action == "hotkey":
                if not req.keys:
                    raise ValueError("keys is required for hotkey")
                pyautogui.hotkey(*req.keys)
                logger.info("[Keyboard] hotkey: %s", "+".join(req.keys))

            else:
                raise ValueError(f"Unknown keyboard action: {req.action!r}")

            duration_ms = int((time.monotonic() - t0) * 1000)

            # Screenshot after
            after_b64: Optional[str] = None
            if req.screenshot_after:
                time.sleep(0.15)  # Allow UI to react
                after_b64 = _take_screenshot()

            return {
                "request_id": request_id,
                "action": req.action,
                "approved": True,
                "success": True,
                "duration_ms": duration_ms,
                "screenshot_before": before_b64,
                "screenshot_after": after_b64,
            }

        except Exception as e:
            logger.error("[Keyboard] %s failed: %s", req.action, e)
            return {
                "request_id": request_id,
                "action": req.action,
                "approved": True,
                "success": False,
                "error": str(e),
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }

    # --------------------------------------------------------------- /api/screenshot

    @app.get("/api/screenshot")
    async def take_screenshot() -> dict:
        """Capture current screen. Returns base64-encoded PNG."""
        b64 = _take_screenshot()
        if b64 is None:
            raise HTTPException(503, "Screenshot unavailable")
        return {"success": True, "image_base64": b64, "format": "png"}

    return app


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def start_local_server(host: str = "127.0.0.1", port: int = 7070) -> None:
    """Start the local HTTP server (blocking). Call from a thread."""
    if not HAS_FASTAPI:
        logger.error("fastapi/uvicorn not installed — run: pip install fastapi uvicorn")
        return

    cfg = load_config()
    port = cfg.get("dashboard_port", port)

    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    logger.info("🌐 Local agent API starting on http://%s:%d", host, port)
    import asyncio
    asyncio.run(server.serve())


def start_local_server_thread(host: str = "127.0.0.1", port: int = 7070) -> threading.Thread:
    """Start the local HTTP server in a background daemon thread."""
    t = threading.Thread(target=start_local_server, args=(host, port), daemon=True, name="LocalAPIServer")
    t.start()
    return t
