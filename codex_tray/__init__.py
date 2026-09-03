"""Codex Tray package."""


def main(*args, **kwargs):
    """Start the default Qt tray UI without importing Qt at package import time."""
    from .tray_qt import main as _main
    return _main(*args, **kwargs)

__all__ = ["main"]
