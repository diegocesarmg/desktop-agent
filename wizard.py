#!/usr/bin/env python3
"""GCC Desktop Agent — Installation Wizard.

Tkinter wizard with steps:
  Welcome → Connection → Storage → ScreenPipe → Settings → Auto-start → Done
"""

import os
import platform
import subprocess
import sys
import tkinter as tk

# Fix console encoding for emoji/unicode on Windows (cp1252 systems)
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

from config import DEFAULT_CONFIG, save_config

# ── Colours & Style ────────────────────────────────────────────────

BG = "#1a1a2e"
BG_CARD = "#16213e"
FG = "#e0e0e0"
ACCENT = "#8B5CF6"
ACCENT_HOVER = "#7C3AED"
SUCCESS = "#10B981"
ERROR = "#EF4444"
MUTED = "#9CA3AF"
FONT = ("Segoe UI", 11)
FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 14, "bold")
FONT_SMALL = ("Segoe UI", 9)


# ── Helpers ─────────────────────────────────────────────────────────

def _style_entry(entry: tk.Entry) -> None:
    entry.configure(
        bg="#0f3460", fg=FG, insertbackground=FG,
        relief="flat", highlightthickness=1,
        highlightcolor=ACCENT, highlightbackground="#374151",
        font=FONT,
    )


def _label(parent, text, **kw) -> tk.Label:
    defaults = dict(bg=BG_CARD, fg=FG, font=FONT)
    defaults.update(kw)
    lbl = tk.Label(parent, text=text, **defaults)
    return lbl


def _heading(parent, text) -> tk.Label:
    return _label(parent, text, font=FONT_H2)


def _button(parent, text, command, primary=True, **kw) -> tk.Button:
    bg = ACCENT if primary else BG_CARD
    fg_ = "#fff" if primary else FG
    btn = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg_, activebackground=ACCENT_HOVER,
        activeforeground="#fff", relief="flat", padx=20, pady=8,
        font=FONT, cursor="hand2", **kw,
    )
    return btn


# ── Steps ───────────────────────────────────────────────────────────

class WizardStep(tk.Frame):
    """Base class for wizard steps."""

    title = ""

    def __init__(self, master, wizard: "Wizard"):
        super().__init__(master, bg=BG_CARD)
        self.wizard = wizard

    def validate(self) -> bool:
        """Return True if step data is valid and we can proceed."""
        return True

    def on_enter(self) -> None:
        """Called when step becomes visible."""
        pass

    def collect(self) -> dict:
        """Return dict of config keys this step sets."""
        return {}


class WelcomeStep(WizardStep):
    title = "Welcome"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        _heading(self, "Welcome to GCC Desktop Agent").pack(pady=(40, 10))
        _label(self, "This wizard will guide you through the setup.", fg=MUTED).pack()
        _label(self, "").pack(pady=5)
        lines = [
            "① Connect to your GCC server",
            "② Choose local storage location",
            "③ Configure ScreenPipe integration",
            "④ Adjust agent settings",
            "⑤ Set up auto-start",
        ]
        for line in lines:
            _label(self, line, font=FONT, anchor="w").pack(fill="x", padx=60, pady=2)

        _label(
            self,
            f"Platform: {platform.system()} {platform.release()}  •  Python {platform.python_version()}",
            fg=MUTED, font=FONT_SMALL,
        ).pack(side="bottom", pady=15)


class ConnectionStep(WizardStep):
    title = "Connection"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        _heading(self, "Server Connection").pack(pady=(30, 20))

        _label(self, "API URL").pack(anchor="w", padx=40)
        self.url_var = tk.StringVar(value=DEFAULT_CONFIG["api_url"])
        e1 = tk.Entry(self, textvariable=self.url_var, width=50)
        _style_entry(e1)
        e1.pack(padx=40, pady=(0, 15), ipady=4)

        _label(self, "API Key").pack(anchor="w", padx=40)
        self.key_var = tk.StringVar()
        e2 = tk.Entry(self, textvariable=self.key_var, width=50, show="•")
        _style_entry(e2)
        e2.pack(padx=40, pady=(0, 15), ipady=4)

        self.status_label = _label(self, "", fg=MUTED, font=FONT_SMALL)
        self.status_label.pack(pady=5)

        _button(self, "Test Connection", self._test, primary=False).pack(pady=5)

    def _test(self):
        url = self.url_var.get().rstrip("/")
        key = self.key_var.get().strip()
        if not url or not key:
            self.status_label.configure(text="Please fill both fields.", fg=ERROR)
            return
        if requests is None:
            self.status_label.configure(text="'requests' not installed — skipping test.", fg=MUTED)
            return
        try:
            r = requests.get(f"{url}/api/health", headers={"X-API-Key": key}, timeout=5)
            if r.status_code < 400:
                self.status_label.configure(text="✓ Connected successfully!", fg=SUCCESS)
            else:
                self.status_label.configure(text=f"✗ Server returned {r.status_code}", fg=ERROR)
        except Exception as exc:
            self.status_label.configure(text=f"✗ {exc}", fg=ERROR)

    def validate(self):
        if not self.url_var.get().strip():
            messagebox.showwarning("Validation", "API URL is required.")
            return False
        if not self.key_var.get().strip():
            messagebox.showwarning("Validation", "API Key is required.")
            return False
        return True

    def collect(self):
        return {
            "api_url": self.url_var.get().strip().rstrip("/"),
            "api_key": self.key_var.get().strip(),
        }


class StorageStep(WizardStep):
    title = "Storage"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        _heading(self, "Local Storage").pack(pady=(30, 10))
        _label(self, "Choose where the agent stores data locally.", fg=MUTED).pack(pady=(0, 20))

        frame = tk.Frame(self, bg=BG_CARD)
        frame.pack(padx=40, fill="x")

        self.path_var = tk.StringVar(value=DEFAULT_CONFIG["storage_path"])
        e = tk.Entry(frame, textvariable=self.path_var, width=42)
        _style_entry(e)
        e.pack(side="left", ipady=4)

        _button(frame, "Browse…", self._browse, primary=False).pack(side="left", padx=(10, 0))

        self.info_label = _label(self, "", fg=MUTED, font=FONT_SMALL)
        self.info_label.pack(pady=15)

    def _browse(self):
        d = filedialog.askdirectory(title="Select storage directory")
        if d:
            self.path_var.set(d)

    def on_enter(self):
        p = Path(self.path_var.get())
        if p.exists():
            self.info_label.configure(text=f"Directory exists. Current size: {_dir_size(p)}")
        else:
            self.info_label.configure(text="Directory will be created.")

    def validate(self):
        if not self.path_var.get().strip():
            messagebox.showwarning("Validation", "Storage path is required.")
            return False
        return True

    def collect(self):
        return {"storage_path": self.path_var.get().strip()}


class ScreenPipeStep(WizardStep):
    title = "ScreenPipe"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        _heading(self, "ScreenPipe Integration").pack(pady=(30, 10))
        _label(self, "ScreenPipe captures screen data for context-aware AI.", fg=MUTED).pack(pady=(0, 15))

        self.enabled_var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(
            self, text="Enable ScreenPipe integration",
            variable=self.enabled_var, bg=BG_CARD, fg=FG,
            selectcolor=BG, activebackground=BG_CARD, activeforeground=FG,
            font=FONT, command=self._toggle,
        )
        cb.pack(anchor="w", padx=40, pady=5)

        self.detail_frame = tk.Frame(self, bg=BG_CARD)
        self.detail_frame.pack(fill="x", padx=40, pady=10)

        _label(self.detail_frame, "ScreenPipe Port").pack(anchor="w")
        self.port_var = tk.StringVar(value="3030")
        e1 = tk.Entry(self.detail_frame, textvariable=self.port_var, width=10)
        _style_entry(e1)
        e1.pack(anchor="w", ipady=4, pady=(0, 10))

        _label(self.detail_frame, "ScreenPipe Executable Path (optional)").pack(anchor="w")
        self.sp_path_var = tk.StringVar()
        e2 = tk.Entry(self.detail_frame, textvariable=self.sp_path_var, width=50)
        _style_entry(e2)
        e2.pack(anchor="w", ipady=4)

        self.detail_frame.pack_forget()

    def _toggle(self):
        if self.enabled_var.get():
            self.detail_frame.pack(fill="x", padx=40, pady=10)
        else:
            self.detail_frame.pack_forget()

    def collect(self):
        port = 3030
        try:
            port = int(self.port_var.get())
        except ValueError:
            pass
        return {
            "screenpipe_enabled": self.enabled_var.get(),
            "screenpipe_port": port,
            "screenpipe_path": self.sp_path_var.get().strip(),
        }


class SettingsStep(WizardStep):
    title = "Settings"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        _heading(self, "Agent Settings").pack(pady=(30, 20))

        _label(self, "Agent Name (displayed in GCC dashboard)").pack(anchor="w", padx=40)
        self.name_var = tk.StringVar(value=platform.node())
        e1 = tk.Entry(self, textvariable=self.name_var, width=40)
        _style_entry(e1)
        e1.pack(padx=40, ipady=4, pady=(0, 15), anchor="w")

        _label(self, "Check Interval (seconds)").pack(anchor="w", padx=40)
        self.interval_var = tk.StringVar(value="60")
        e2 = tk.Entry(self, textvariable=self.interval_var, width=10)
        _style_entry(e2)
        e2.pack(padx=40, ipady=4, pady=(0, 15), anchor="w")

        _label(self, "Log Level").pack(anchor="w", padx=40)
        self.log_var = tk.StringVar(value="INFO")
        combo = ttk.Combobox(
            self, textvariable=self.log_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            state="readonly", width=15,
        )
        combo.pack(padx=40, anchor="w")

    def validate(self):
        try:
            v = int(self.interval_var.get())
            if v < 5:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Validation", "Check interval must be an integer ≥ 5.")
            return False
        return True

    def collect(self):
        return {
            "agent_name": self.name_var.get().strip(),
            "check_interval_seconds": int(self.interval_var.get()),
            "log_level": self.log_var.get(),
        }


class AutoStartStep(WizardStep):
    title = "Auto-start"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        _heading(self, "Auto-start on Login").pack(pady=(30, 10))

        system = platform.system()
        note = {
            "Windows": "Adds a shortcut to your Startup folder.",
            "Darwin": "Creates a LaunchAgent plist.",
            "Linux": "Creates a systemd user service or XDG autostart entry.",
        }.get(system, "Auto-start may not be available on this platform.")
        _label(self, note, fg=MUTED, wraplength=400).pack(pady=(0, 20))

        self.auto_var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(
            self, text="Start GCC Desktop Agent when I log in",
            variable=self.auto_var, bg=BG_CARD, fg=FG,
            selectcolor=BG, activebackground=BG_CARD, activeforeground=FG,
            font=FONT,
        )
        cb.pack(anchor="w", padx=40)

    def collect(self):
        return {"auto_start": self.auto_var.get()}


class DoneStep(WizardStep):
    title = "Done"

    def __init__(self, master, wizard):
        super().__init__(master, wizard)
        self.check = _label(self, "✓", fg=SUCCESS, font=("Segoe UI", 48, "bold"))
        self.check.pack(pady=(40, 10))
        _heading(self, "Setup Complete!").pack()
        _label(self, "Your configuration has been saved.", fg=MUTED).pack(pady=5)
        self.path_label = _label(self, "", fg=MUTED, font=FONT_SMALL)
        self.path_label.pack(pady=5)

    def on_enter(self):
        from config import CONFIG_FILE
        self.path_label.configure(text=f"Config: {CONFIG_FILE}")


# ── Wizard Controller ───────────────────────────────────────────────

class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GCC Desktop Agent — Setup")
        self.geometry("600x520")
        self.configure(bg=BG)
        self.resizable(False, False)

        # Header
        header = tk.Frame(self, bg=BG, height=50)
        header.pack(fill="x")
        _label(header, "⚡ GCC Desktop Agent", bg=BG, font=FONT_H1, fg=ACCENT).pack(
            side="left", padx=20, pady=10,
        )

        # Step indicator
        self.step_indicator = tk.Frame(self, bg=BG)
        self.step_indicator.pack(fill="x", padx=20)

        # Content
        self.container = tk.Frame(self, bg=BG_CARD, highlightthickness=1, highlightbackground="#374151")
        self.container.pack(fill="both", expand=True, padx=20, pady=(5, 0))

        # Navigation
        nav = tk.Frame(self, bg=BG)
        nav.pack(fill="x", padx=20, pady=15)
        self.back_btn = _button(nav, "← Back", self._back, primary=False)
        self.back_btn.pack(side="left")
        self.next_btn = _button(nav, "Next →", self._next)
        self.next_btn.pack(side="right")

        # Build steps
        self.steps: list[WizardStep] = [
            WelcomeStep(self.container, self),
            ConnectionStep(self.container, self),
            StorageStep(self.container, self),
            ScreenPipeStep(self.container, self),
            SettingsStep(self.container, self),
            AutoStartStep(self.container, self),
            DoneStep(self.container, self),
        ]
        self.current = 0
        self._show_step(0)

    def _build_indicator(self):
        for w in self.step_indicator.winfo_children():
            w.destroy()
        for i, step in enumerate(self.steps):
            fg_ = ACCENT if i == self.current else (SUCCESS if i < self.current else MUTED)
            marker = "●" if i == self.current else ("✓" if i < self.current else "○")
            _label(
                self.step_indicator, f"{marker} {step.title}",
                bg=BG, fg=fg_, font=FONT_SMALL,
            ).pack(side="left", padx=6)

    def _show_step(self, idx: int):
        for s in self.steps:
            s.pack_forget()
        self.current = idx
        step = self.steps[idx]
        step.pack(fill="both", expand=True)
        step.on_enter()
        self._build_indicator()

        is_last = idx == len(self.steps) - 1
        is_first = idx == 0
        self.back_btn.configure(state="normal" if not is_first else "disabled")
        if is_last:
            self.next_btn.configure(text="Finish", command=self.destroy)
        else:
            self.next_btn.configure(text="Next →", command=self._next)

    def _next(self):
        step = self.steps[self.current]
        if not step.validate():
            return
        if self.current < len(self.steps) - 1:
            # On the step before Done, save config
            if self.current == len(self.steps) - 2:
                self._save()
            self._show_step(self.current + 1)

    def _back(self):
        if self.current > 0:
            self._show_step(self.current - 1)

    def _save(self):
        config = dict(DEFAULT_CONFIG)
        for step in self.steps:
            config.update(step.collect())
        save_config(config)


# ── Utility ─────────────────────────────────────────────────────────

def _dir_size(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    for unit in ("B", "KB", "MB", "GB"):
        if total < 1024:
            return f"{total:.1f} {unit}"
        total /= 1024
    return f"{total:.1f} TB"


if __name__ == "__main__":
    app = Wizard()
    app.mainloop()
