# GCC Desktop Agent

Desktop agent for the GCC (Gucci Command Center) ecosystem. Runs on Windows/macOS/Linux.

## Components

- **Installation Wizard** (`wizard.py`) — Guided setup via Tkinter
- **Tray Icon** (`tray.py`) — System tray with color-coded status + WebSocket connection
- **Shell Executor** (`shell_executor.py`) — Multi-shell command execution (PowerShell, CMD, WSL2, Bash) with sandboxing, timeouts, streaming output, and per-mission permissions
- **Desktop Control** (`desktop_control.py`) — Screenshot, click, type, hotkeys, window management via pyautogui with per-mission permission checks
- **Mission Manager** (`mission_manager.py`) — CRUD for missions, YOLO mode, command tracking, backend API sync
- **Multi-desktop Registration** (`registration.py`) — Agent registration with unique labels, heartbeat, status management, dashboard integration
- **Legacy Executor** (`executor.py`) — Original shell executor (kept for compatibility)

## Quick Start

```bash
pip install -r requirements.txt

# Run the setup wizard first
python wizard.py

# Then start the agent
python tray.py
```

## Architecture

```
tray.py (main thread: pystray icon)
  └─ background thread: asyncio event loop
       ├─ WebSocketClient (persistent connection + heartbeat)
       ├─ RegistrationManager (agent registration + heartbeat)
       └─ MissionManager (mission CRUD + sync)
            ├─ ShellExecutor (PS/CMD/WSL2/Bash command execution)
            └─ DesktopController (screenshot/click/type/hotkeys/windows)
```

## Shell Executor

Supports three execution modes:
- **Assisted**: Every command requires user approval
- **YOLO**: All commands run without approval
- **Whitelist**: Pre-approved command prefixes run automatically; others need approval

Shell types: PowerShell, CMD, WSL2, Bash, SH (auto-detected per platform).

Per-mission permissions:
- Allowed/blocked command patterns
- Execution mode override
- Timeout caps

## Desktop Control

Actions: screenshot, click, double-click, right-click, type, hotkeys, scroll, drag, mouse position, screen size, window management (focus/minimize/maximize/close/resize/move).

Per-mission permissions:
- `safe_only`: Only read-only actions (screenshot, get position, list windows)
- `allow_input`: Control click/type/hotkey access
- `allow_window_mgmt`: Control window operation access

## Mission Manager

- Full CRUD for missions
- YOLO mode toggle per mission
- Command history tracking with stats
- Local persistence (JSON)
- Backend API sync (push + pull)
- Permission bridge to shell executor and desktop controller

## Multi-desktop Registration

- Unique agent ID derived from label + machine info
- Auto-detection of capabilities (shells, desktop control, WSL2)
- Registration, heartbeat, and deregistration with GCC API
- Status management (online/offline/busy/error)
- List all registered agents for dashboard

## Configuration

Config stored in `~/.gcc-agent/config.json`. Key fields:

| Field | Description |
|-------|-------------|
| `api_url` | GCC API base URL |
| `api_key` | Authentication key |
| `agent_name` | Display name for this agent |
| `executor_mode` | Shell execution mode (assisted/yolo/whitelist) |
| `log_level` | Logging verbosity |

## Requirements

- Python 3.10+
- tkinter (for wizard)
- pyautogui (for desktop control — optional on headless systems)
- System tray support (for tray icon)
