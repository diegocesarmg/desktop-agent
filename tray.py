"""GCC Desktop Agent — System Tray Icon + WebSocket connection.

Provides a persistent system tray icon with status indicator and a
WebSocket connection to the GCC Bridge Server with automatic reconnect
and periodic heartbeat.
"""

import asyncio
import json
import logging
import platform
import signal
import sys
import threading
import time
from enum import Enum
from typing import Optional

import pystray
from PIL import Image, ImageDraw

from config import load_config
from updater import AutoUpdater

logger = logging.getLogger("gcc-agent")

# ---------------------------------------------------------------------------
# Connection state
# ---------------------------------------------------------------------------

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Icon generation (colored circle indicators)
# ---------------------------------------------------------------------------

ICON_COLORS = {
    ConnectionState.DISCONNECTED: "#6B7280",  # gray
    ConnectionState.CONNECTING: "#F59E0B",     # amber
    ConnectionState.CONNECTED: "#10B981",      # green
    ConnectionState.ERROR: "#EF4444",          # red
}


def _create_icon_image(state: ConnectionState, size: int = 64) -> Image.Image:
    """Create a simple colored-circle tray icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = ICON_COLORS[state]
    margin = size // 8
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)
    # Inner "G" text hint
    try:
        draw.text((size // 2 - 6, size // 2 - 8), "G", fill="white")
    except Exception:
        pass
    return img


# ---------------------------------------------------------------------------
# WebSocket client with reconnect + heartbeat
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL = 30  # seconds
RECONNECT_BASE_DELAY = 2  # seconds
RECONNECT_MAX_DELAY = 60  # seconds


class WebSocketClient:
    """Manages a persistent WebSocket connection with reconnect & heartbeat."""

    def __init__(self, config: dict, on_state_change=None):
        self.config = config
        self.on_state_change = on_state_change
        self._state = ConnectionState.DISCONNECTED
        self._ws = None
        self._stop_event = asyncio.Event()
        self._reconnect_delay = RECONNECT_BASE_DELAY

    @property
    def state(self) -> ConnectionState:
        return self._state

    def _set_state(self, state: ConnectionState):
        if state != self._state:
            self._state = state
            logger.info("Connection state → %s", state.value)
            if self.on_state_change:
                try:
                    self.on_state_change(state)
                except Exception:
                    pass

    def _ws_url(self) -> str:
        api_url = self.config.get("api_url", "https://gcc-api.devloopment.com")
        # Convert http(s) to ws(s)
        ws_url = api_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{ws_url}/ws/agent"

    async def run(self):
        """Main loop: connect → heartbeat → reconnect on failure."""
        import websockets  # lazy import

        while not self._stop_event.is_set():
            url = self._ws_url()
            self._set_state(ConnectionState.CONNECTING)
            try:
                headers = {}
                api_key = self.config.get("api_key", "")
                if api_key:
                    headers["X-API-Key"] = api_key
                agent_name = self.config.get("agent_name", platform.node())
                headers["X-Agent-Name"] = agent_name

                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._set_state(ConnectionState.CONNECTED)
                    self._reconnect_delay = RECONNECT_BASE_DELAY  # reset on success
                    logger.info("Connected to %s", url)

                    # Send initial hello
                    await ws.send(json.dumps({
                        "type": "hello",
                        "agent": agent_name,
                        "platform": platform.system(),
                        "version": "0.1.0",
                    }))

                    # Heartbeat + receive loop
                    await asyncio.gather(
                        self._heartbeat_loop(ws),
                        self._receive_loop(ws),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._set_state(ConnectionState.ERROR)
                logger.warning("WebSocket error: %s — reconnecting in %ds",
                               exc, self._reconnect_delay)

            self._ws = None

            if self._stop_event.is_set():
                break

            # Wait before reconnecting (with exponential backoff)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._reconnect_delay,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass
            self._reconnect_delay = min(
                self._reconnect_delay * 2, RECONNECT_MAX_DELAY
            )

        self._set_state(ConnectionState.DISCONNECTED)

    async def _heartbeat_loop(self, ws):
        """Send periodic heartbeats."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await ws.send(json.dumps({
                    "type": "heartbeat",
                    "ts": time.time(),
                    "agent": self.config.get("agent_name", platform.node()),
                }))
                logger.debug("Heartbeat sent")
            except Exception:
                return  # connection lost, let outer loop handle it

    async def _receive_loop(self, ws):
        """Receive and log messages from the server."""
        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                    logger.info("Received: %s", data.get("type", "unknown"))
                    # Future: dispatch commands from server
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message: %s", message[:200])
        except Exception:
            return  # connection lost

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------

class TrayApp:
    """System tray icon with status and controls."""

    def __init__(self):
        self.config = load_config()
        self._state = ConnectionState.DISCONNECTED
        self._icon: Optional[pystray.Icon] = None
        self._ws_client = WebSocketClient(
            self.config, on_state_change=self._on_ws_state_change
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._updater = AutoUpdater(self.config)
        self._checking_updates = False

    def _on_ws_state_change(self, state: ConnectionState):
        self._state = state
        if self._icon:
            self._icon.icon = _create_icon_image(state)
            self._icon.title = f"GCC Agent — {state.value}"

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                lambda _: f"Status: {self._state.value}",
                action=None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Check for Updates", self._on_check_updates),
            pystray.MenuItem("Reconnect", self._on_reconnect),
            pystray.MenuItem("Quit", self._on_quit),
        )

    # -- Menu actions -------------------------------------------------------

    def _on_check_updates(self, icon, item):
        """Triggered by the 'Check for Updates' tray menu item."""
        if self._checking_updates:
            logger.debug("Update check already in progress — ignoring duplicate request")
            return
        logger.info("Manual update check requested")
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _run_update_check(self, *, notify: bool = True) -> tuple[str, str]:
        """Run update check and optionally show a notification.

        Can be called from any thread.  Returns (status, message).
        """
        self._checking_updates = True
        try:
            status, message = self._updater.check()
        finally:
            self._checking_updates = False

        if notify and self._icon:
            title = "GCC Agent — Updates"
            try:
                self._icon.notify(message, title)
            except Exception as exc:
                # notify() not supported on all platforms — fall back to log
                logger.info("Update check result: %s", message)
                logger.debug("Notification not available: %s", exc)
        return status, message

    def _on_reconnect(self, icon, item):
        logger.info("Manual reconnect requested")
        self._restart_ws()

    def _on_quit(self, icon, item):
        logger.info("Quit requested")
        self._ws_client.stop()
        if self._icon:
            self._icon.stop()

    # -- WebSocket thread ---------------------------------------------------

    def _ws_thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_client.run())
        finally:
            self._loop.close()

    def _restart_ws(self):
        # Stop existing
        self._ws_client.stop()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        # Create fresh client + thread
        self._ws_client = WebSocketClient(
            self.config, on_state_change=self._on_ws_state_change
        )
        self._ws_thread = threading.Thread(
            target=self._ws_thread_main, daemon=True
        )
        self._ws_thread.start()

    # -- Entry point --------------------------------------------------------

    def run(self):
        """Start tray icon + WS connection."""
        logging.basicConfig(
            level=self.config.get("log_level", "INFO"),
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        )

        # Start WebSocket in background thread
        self._ws_thread = threading.Thread(
            target=self._ws_thread_main, daemon=True
        )
        self._ws_thread.start()

        # Start tray icon (blocks on main thread)
        self._icon = pystray.Icon(
            name="gcc-agent",
            icon=_create_icon_image(self._state),
            title="GCC Agent — disconnected",
            menu=self._build_menu(),
        )

        # Kick off startup update check (non-blocking, via pystray setup callback)
        def _startup(icon: pystray.Icon) -> None:
            """Called by pystray once the icon event loop is ready."""
            icon.visible = True
            # Give the icon a moment to render before showing a notification
            time.sleep(1)
            logger.info("Startup update check…")
            self._run_update_check(notify=True)

        logger.info("Starting tray icon…")
        self._icon.run(setup=_startup)

        # Cleanup after tray exits
        self._ws_client.stop()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        logger.info("GCC Agent stopped.")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main():
    app = TrayApp()

    # Graceful Ctrl+C
    def _sig_handler(sig, frame):
        app._ws_client.stop()
        if app._icon:
            app._icon.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    app.run()


if __name__ == "__main__":
    main()
