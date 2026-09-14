"""Tray UI built with PySide6: a system-tray icon whose PNG shows the
remaining-percent number on a transparent background, plus a multi-line
tooltip with full details.

The icon PNG is auto-scaled by Windows to its tray pixel size. We render
it large so the number stays readable at every DPI.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import traceback
import webbrowser

# A PyInstaller windowed executable intentionally has no stderr stream.
# Enabling faulthandler without a target stream raises at import time.
if sys.stderr is not None:
    faulthandler.enable()


def _excepthook(exc_type, exc_value, exc_tb):
    logging.getLogger("codex_tray").error(
        "UNCAUGHT EXCEPTION:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    if sys.stderr is not None:
        sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _excepthook

from PySide6.QtGui import (
    QAction, QIcon, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QSystemTrayIcon, QVBoxLayout,
)

from . import credentials as creds_mod
from . import autostart
from . import icon as icon_mod
from .client import MockClient, RateLimitClient
from .http_client import HttpsClient
from .poller import Poller
from .snapshot import Snapshot

APP_NAME = "Codex Tray"
REFRESH_CHATGPT_URL = "https://chatgpt.com/"
ANALYTICS_URL = "https://chatgpt.com/codex/cloud/settings/analytics"
POLL_INTERVAL = 60

log = logging.getLogger(__name__)


def _render_icon(text: str, percent_for_gauge: float | None,
                 status: str = "ok") -> QIcon:
    result = QIcon()
    for img in icon_mod.render_dpi_set(
        text=text,
        status=status,
        gauge_percent=percent_for_gauge,
    ).values():
        pix = QPixmap()
        pix.loadFromData(icon_mod.to_ico_bytes(img), "PNG")
        result.addPixmap(pix)
    return result


def _percent_str(pct: float | None, fallback: str = "?") -> str:
    if pct is None:
        return fallback
    p = int(round(pct))
    if p <= 0:
        return "0%"
    if p >= 100:
        return "100%"
    return f"{p}%"


class CodexTrayApp:
    def __init__(self, client: RateLimitClient):
        self.client = client
        self.tray = None
        self.poller = None
        self.app = None

    def run(self):
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)

        self._init_tray_icon()

        self.poller = Poller(client=self.client, interval_s=POLL_INTERVAL)

        def _on_new(snap: Snapshot) -> None:
            self._refresh(snap)

        self.poller.on_update(_on_new)
        self.poller.start()
        log.info("starting Qt tray loop")
        try:
            self.app.exec()
        except Exception:
            log.exception("Qt main loop crashed")
            raise

    # ----- menu callbacks -----
    def refresh_now(self) -> None:
        if self.poller:
            snap = self.poller.refresh_once()
            self._refresh(snap)

    def quit_app(self) -> None:
        if self.poller:
            self.poller.stop()
        if self.app:
            self.app.quit()

    def open_analytics(self) -> None:
        webbrowser.open(ANALYTICS_URL)

    def import_credentials(self) -> None:
        """Import a DevTools cURL without requiring Python on the target PC."""
        dialog = QDialog()
        dialog.setWindowTitle("导入 ChatGPT 登录凭据")
        dialog.resize(680, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "在浏览器中登录 chatgpt.com，打开开发者工具 → Network，\n"
            "选择任意 chatgpt.com 请求，右键 Copy → Copy as cURL (bash)，"
            "然后粘贴到下面。"
        ))
        pasted = QPlainTextEdit()
        pasted.setPlaceholderText("在此粘贴 Copy as cURL (bash) 的完整内容…")
        layout.addWidget(pasted, 1)
        layout.addWidget(QLabel("代理地址（可留空，例如 http://127.0.0.1:7890）："))
        proxy = QLineEdit()
        proxy.setPlaceholderText("留空表示直连")
        try:
            existing = creds_mod.load()
            proxy.setText(existing.get("proxy_url") or "")
        except Exception:
            proxy.setText(os.environ.get("CODEX_PROXY") or "")
        layout.addWidget(proxy)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        try:
            from from_pasted import (
                extract_session_details,
                fetch_url,
                parse_pasted,
            )
            parsed = parse_pasted(pasted.toPlainText())
            proxy_url = proxy.text().strip()
            cookies = [
                {
                    "name": name,
                    "value": value,
                    "domain": ".chatgpt.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "Lax",
                }
                for name, value in parsed["cookies"]
            ]
            session_status, session_body = fetch_url(
                "https://chatgpt.com/api/auth/session",
                parsed["cookies"],
                parsed["user_agent"],
                parsed["origin"],
                parsed["referer"],
                proxy_url=proxy_url,
            )
            access_token, _user_email, _user_id = extract_session_details(
                session_body
            )
            if session_status != 200 or not access_token:
                raise RuntimeError(
                    "Cookie import succeeded, but ChatGPT did not return an "
                    f"access token (HTTP {session_status}). Copy a fresh "
                    "chatgpt.com request and try again."
                )
            creds_mod.save({
                "cookies": cookies,
                "access_token": access_token,
                "user_agent": parsed["user_agent"],
                "proxy_url": proxy_url,
            })
        except BaseException as exc:
            QMessageBox.critical(
                dialog, "导入失败", str(exc) or exc.__class__.__name__
            )
            return
        QMessageBox.information(
            dialog,
            "导入成功",
            f"已加密保存 {len(cookies)} 个 Cookie。\n请退出并重新启动 Codex Tray。",
        )

    def set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except Exception as exc:
            QMessageBox.warning(
                None, "设置失败", f"无法更新开机自启动：\n{exc}"
            )

    # ----- internals -----
    def _init_tray_icon(self) -> None:
        self.tray = QSystemTrayIcon()
        self.tray.setToolTip(f"{APP_NAME}\nloading…")
        self.tray.setIcon(_render_icon("?", percent_for_gauge=None))
        self.tray.setVisible(True)

        menu = QMenu()
        a1 = QAction("立刻刷新", menu)
        a1.triggered.connect(self.refresh_now)
        menu.addAction(a1)
        a2 = QAction("打开 Codex analytics", menu)
        a2.triggered.connect(self.open_analytics)
        menu.addAction(a2)
        a3 = QAction("导入/更新登录凭据…", menu)
        a3.triggered.connect(self.import_credentials)
        menu.addAction(a3)
        startup_action = QAction("开机自动启动", menu)
        startup_action.setCheckable(True)
        try:
            startup_action.setChecked(autostart.is_enabled())
        except Exception:
            startup_action.setChecked(False)
        startup_action.toggled.connect(self.set_autostart)
        menu.addAction(startup_action)
        menu.addSeparator()
        a4 = QAction("退出", menu)
        a4.triggered.connect(self.quit_app)
        menu.addAction(a4)
        self.tray.setContextMenu(menu)

        self.tray.activated.connect(self._tray_activated)

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_analytics()

    def _refresh(self, snap: Snapshot) -> None:
        if not self.tray:
            return

        # Decide icon contents.
        if snap.needs_relogin:
            text, gauge_pct, status = "?", None, "expired"
        elif not snap.ok or not snap.models:
            text, gauge_pct, status = "?", None, "err"
        else:
            primary = next(
                (m for m in snap.models if m.reset_seconds > 0),
                snap.models[0],
            )
            pct = primary.remaining_percent
            if pct is None and primary.limit > 0:
                pct = max(0.0, 100.0 * primary.remaining / primary.limit)
            text = _percent_str(pct, fallback="?").rstrip("%") or "?"
            gauge_pct = pct if pct is not None else None
            status = "ok"

        self.tray.setIcon(_render_icon(text, gauge_pct, status))
        self.tray.setToolTip(snap.tooltip())


def _make_client(proxy_url: str | None = None) -> tuple[RateLimitClient, str]:
    try:
        data = creds_mod.load()
    except FileNotFoundError:
        return MockClient(), "mock (no credentials; run `python from_pasted.py`)"
    except Exception as exc:
        log.warning("could not load credentials: %s", exc)
        return MockClient(), f"mock (creds unreadable: {exc})"
    effective_proxy = (
        proxy_url if proxy_url is not None else data.get("proxy_url") or ""
    )
    return (
        HttpsClient(
            access_token=data.get("access_token"),
            cookies=data.get("cookies"),
            user_agent=data.get("user_agent"),
            proxy_url=effective_proxy,
        ),
        "real",
    )


def main(client: RateLimitClient | None = None, proxy_url: str | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        autostart.enable_by_default()
    except Exception as exc:
        log.warning("could not enable per-user autostart: %s", exc)
    if client is None:
        client, mode = _make_client(proxy_url=proxy_url)
    else:
        mode = "injected"
    log.info("client mode: %s", mode)

    app = CodexTrayApp(client=client)
    app.run()
