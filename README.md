# GCC Desktop Agent

Desktop agent for the GCC (Gucci Command Center) ecosystem. Runs on Windows/macOS/Linux.

## Components

- **Installation Wizard** (`wizard.py`) — Guided setup: Welcome → Connection → Storage → ScreenPipe → Settings → Auto-start → Done
- **Tray Icon** (`tray.py`) — System tray with color-coded status indicator (green=connected, amber=connecting, gray=disconnected, red=error)
- **WebSocket Client** (in `tray.py`) — Persistent connection to GCC Bridge Server with auto-reconnect (exponential backoff) and periodic heartbeat
- **Executor** (planned) — Local task execution engine
- **ScreenPipe Proxy** (planned) — Local ScreenPipe data relay

## Quick Start

```bash
pip install -r requirements.txt

# Run the setup wizard first
python wizard.py

# Then start the agent
python tray.py
```

## Requirements

- Python 3.10+
- tkinter (usually bundled with Python; needed for wizard)
- System tray support (most desktop environments)

## Architecture

```
tray.py (main thread: pystray icon)
  └─ background thread: asyncio event loop
       └─ WebSocketClient.run()
            ├─ connect with X-API-Key + X-Agent-Name headers
            ├─ send "hello" on connect
            ├─ heartbeat every 30s
            ├─ receive loop for server commands
            └─ auto-reconnect with exponential backoff (2s → 60s max)
```

## Configuration

Config is stored in `~/.gcc-agent/config.json` (created by the wizard). Key fields:

| Field | Description |
|-------|-------------|
| `api_url` | GCC API base URL |
| `api_key` | Authentication key |
| `agent_name` | Display name for this agent |
| `log_level` | Logging verbosity (DEBUG/INFO/WARNING) |
