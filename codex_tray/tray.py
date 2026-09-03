"""Tray wiring: connects Poller snapshots to a pystray.Icon."""
from __future__ import annotations

import logging
import webbrowser

import pystray
from pystray import MenuItem as Item

from . import icon
from . import credentials as creds_mod
from .client import MockClient, RateLimitClient
from .http_client import HttpsClient
from .poller import Poller
from .snapshot import Snapshot

log = logging.getLogger(__name__)

APP_NAME = "Codex Tray"
REFRESH_CHATGPT_URL = "https://chatgpt.com/"
POLL_INTERVAL = 60  # seconds


def _color_status(snap: Snapshot) -> tuple[float, str]:
    if snap.needs_relogin:
        return 0.0, "expired"
    if not snap.ok or not snap.models:
        return 0.0, "err"
    worst = min(snap.models, key=lambda m: m.ratio)
    return worst.ratio, "ok"


def _first_remaining_percent(snap: Snapshot) -> float | None:
    """Return the remaining-% from the first model that has one set."""
    if not snap.ok or not snap.models:
        return None
    for m in snap.models:
        if m.used_percent is not None:
            return max(0.0, 100.0 - m.used_percent)
    # fallback: derive from used/limit
    m = snap.models[0]
    if m.limit <= 0:
        return None
    return max(0.0, 100.0 * (1.0 - m.used / m.limit))


def _build_menu(poller: Poller) -> pystray.Menu:
    def status_line(_) -> str:
        snap = poller.snapshot
        if not snap.ok or not snap.models:
            err = snap.error or "no data"
            return f"状态: 错误 ({err})"
        parts = [f"{m.model}: {m.remaining}/{m.limit}" for m in snap.models]
        return "状态: " + " | ".join(parts)

    def refresh_now(icon_obj: pystray.Icon, _: Item) -> None:
        snap = poller.refresh_once()
        log.info("manual refresh: ok=%s models=%d", snap.ok, len(snap.models))

    return pystray.Menu(
        Item("立即刷新", refresh_now),
        Item("打开 ChatGPT", lambda *_: webbrowser.open(REFRESH_CHATGPT_URL)),
        pystray.Menu.SEPARATOR,
        Item(status_line, None, enabled=False),
        pystray.Menu.SEPARATOR,
        Item("退出", lambda icon_obj: icon_obj.stop()),
    )


def _make_client(proxy_url: str | None = None) -> tuple[RateLimitClient, str]:
    """Return a real client if credentials exist; otherwise the mock.

    `proxy_url` overrides whatever's stored in credentials.bin when non-empty.
    Pass an explicit empty string "" to force NO proxy.
    """
    try:
        data = creds_mod.load()
    except FileNotFoundError:
        return MockClient(), "mock (no credentials found; run `python setup.py`)"
    except Exception as exc:
        log.warning("could not load credentials: %s", exc)
        return MockClient(), f"mock (creds unreadable: {exc})"

    effective_proxy = proxy_url if proxy_url is not None else data.get("proxy_url") or ""
    return (
        HttpsClient(
            access_token=data.get("access_token"),
            cookies=data.get("cookies"),
            user_agent=data.get("user_agent"),
            proxy_url=effective_proxy,
        ),
        f"real (proxy={effective_proxy or 'direct'})",
    )


def main(client: RateLimitClient | None = None, proxy_url: str | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if client is None:
        client, mode = _make_client(proxy_url=proxy_url)
    else:
        mode = "injected"
    log.info("client mode: %s", mode)

    poller = Poller(client=client, interval_s=POLL_INTERVAL)

    initial = poller.refresh_once()
    ratio, status = _color_status(initial)
    initial_pct = _first_remaining_percent(initial)
    initial_text = str(int(round(initial_pct))) if initial_pct is not None and status == "ok" else "?"
    icon_obj = pystray.Icon(
        APP_NAME,
        icon=icon.render(text=initial_text, status=status, size_px=48,
                         gauge_percent=initial_pct),
        title=initial.headline(),
        menu=_build_menu(poller),
    )

    def on_new_snapshot(snap: Snapshot) -> None:
        ratio, status = _color_status(snap)
        pct = _first_remaining_percent(snap)
        text = str(int(round(pct))) if pct is not None and status == "ok" else "?"
        try:
            icon_obj.icon = icon.render(text=text, status=status, size_px=48,
                                        gauge_percent=pct)
            icon_obj.title = snap.headline()
            icon_obj.update_menu()
        except Exception:
            log.exception("updating tray from snapshot failed")

    poller.on_update(on_new_snapshot)
    poller.start()
    log.info("starting tray loop")
    icon_obj.run()


if __name__ == "__main__":
    main()
