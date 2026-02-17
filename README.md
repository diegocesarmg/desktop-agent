# GCC Desktop Agent

Desktop agent for the GCC (Gucci Command Center) ecosystem. Runs on Windows/macOS/Linux.

## Components

- **Installation Wizard** (`wizard.py`) — Guided setup: Welcome → Connection → Storage → ScreenPipe → Settings → Auto-start → Done
- **Tray Icon** (planned) — System tray with status indicator
- **Executor** (planned) — Local task execution engine
- **ScreenPipe Proxy** (planned) — Local ScreenPipe data relay

## Quick Start

```bash
pip install -r requirements.txt
python wizard.py
```

## Requirements

- Python 3.10+
- tkinter (usually bundled with Python)
