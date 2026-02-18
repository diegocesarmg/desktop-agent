"""Configuration management for GCC Desktop Agent."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".gcc-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "api_url": "https://gcc-api.devloopment.com",
    "api_key": "",
    "storage_path": str(Path.home() / ".gcc-agent" / "data"),
    "screenpipe_enabled": False,
    "screenpipe_port": 3030,
    "screenpipe_path": "",
    "auto_start": False,
    "agent_name": "",
    "check_interval_seconds": 60,
    "log_level": "INFO",
    # Task #103: Desktop Control — Mouse & Keyboard
    "permission_mode": "assisted",   # "yolo" | "assisted"
    "dashboard_port": 7070,          # Local HTTP server port for GCC API relay
    "dashboard_enabled": True,       # Enable local HTTP API server
}


def load_config() -> dict:
    """Load config from disk, returning defaults for missing keys."""
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict) -> None:
    """Persist config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
