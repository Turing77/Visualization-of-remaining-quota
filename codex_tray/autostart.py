"""Per-user Windows startup registration for the packaged tray app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "CodexTray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STATE_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "CodexTray"
DISABLED_MARKER = STATE_DIR / "autostart.disabled"


def _command() -> str:
    # In a PyInstaller one-file build sys.executable is the persistent outer
    # EXE, not the temporary extraction directory.
    return f'"{Path(sys.executable).resolve()}"'


def _write_registry(command: str) -> None:
    import winreg
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)


def _delete_registry() -> None:
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def set_enabled(enabled: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if enabled:
        if DISABLED_MARKER.exists():
            DISABLED_MARKER.unlink()
        _write_registry(_command())
    else:
        DISABLED_MARKER.write_text("disabled\n", encoding="utf-8")
        _delete_registry()


def enable_by_default() -> None:
    """Enable on first run, while respecting a later explicit opt-out."""
    if os.environ.get("CODEX_TRAY_NO_AUTOSTART") == "1":
        return
    if not DISABLED_MARKER.exists():
        _write_registry(_command())


def is_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return value == _command()
    except FileNotFoundError:
        return False
