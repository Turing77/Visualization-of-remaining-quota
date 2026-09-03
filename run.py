"""Main entrypoint. Default UI is PySide6; `--pystray` falls back to the
original pystray-only tray icon (no floating panel)."""
import argparse
import os
import sys


def _run_qt(args):
    from codex_tray.tray_qt import main
    main(proxy_url=proxy_url)


def _run_pystray(args):
    from codex_tray.tray import main
    main(proxy_url=proxy_url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proxy",
        default=os.environ.get("CODEX_PROXY") or None,
        help="Override the proxy URL stored in credentials.bin. Set to '' to force no proxy.",
    )
    parser.add_argument(
        "--pystray",
        action="store_true",
        help="Use the legacy pystray-only UI (no floating panel).",
    )
    args = parser.parse_args()
    proxy_url = args.proxy
    if proxy_url is not None:
        proxy_url = proxy_url.strip()
    if args.pystray:
        _run_pystray(args)
    else:
        try:
            _run_qt(args)
        except ImportError as exc:
            print(f"[warn] PySide6 import failed: {exc}; falling back to pystray.",
                  file=sys.stderr)
            _run_pystray(args)
