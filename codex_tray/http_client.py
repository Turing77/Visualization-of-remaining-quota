"""HTTP rate-limit client.

`HttpsClient.fetch()` queries `/backend-api/wham/usage` (Codex Cloud's
internal weekly-usage endpoint) using the user's session cookies and UA,
parses the JSON, and returns a Snapshot.

For accounts that don't have Codex Cloud access (e.g. plain ChatGPT Pro),
the endpoint returns 401/403; `fetch()` will try `accounts/check/v4-...`
as a fallback that surfaces the structured account limits.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import urllib.error
import urllib.request

from .parse_wham import parse_wham_usage

log = logging.getLogger(__name__)

WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ACCOUNTS_CHECK_URL = (
    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
)


def _build_opener(proxy_url: str | None):
    """Build a urllib opener, optionally with a proxy.

    For SOCKS5 we require PySocks (https://pypi.org/project/PySocks/).
    """
    if not proxy_url:
        return urllib.request.build_opener()
    if proxy_url.startswith(("socks5://", "socks://")):
        try:
            import socket  # noqa: F401  (PySocks monkey-patches this)
            import socks  # type: ignore[import-not-found]  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "SOCKS5 proxy selected but PySocks is not installed. "
                "Run: pip install PySocks"
            ) from exc
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    return urllib.request.build_opener(handler)


def _http_get(
    url: str,
    headers: dict[str, str],
    timeout: float = 10.0,
    proxy_url: str | None = None,
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = _build_opener(proxy_url)
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec
            raw = resp.read()
            return resp.status, raw.decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return exc.code, body, dict(exc.headers or {})


# ----------------------------------------------------------------------
# Legacy /accounts/check/v4 parser — kept as a fallback for non-Codex accounts.
# ----------------------------------------------------------------------
def _coerce_int(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _parse_accounts_check_v4(body: str) -> list[dict]:
    """Best-effort: emit one synthetic entry per known subscription plan.

    The v4 endpoint no longer exposes per-model numeric quotas in the
    same shape as the legacy /accounts/check payload; we just record the
    plan name so the tray can display something meaningful instead of
    erroring out.
    """
    out: list[dict] = []
    try:
        data = json.loads(body)
    except Exception:
        return out
    if not isinstance(data, dict):
        return out

    accounts = data.get("accounts") or {}
    if isinstance(accounts, dict):
        for _account_id, account in accounts.items():
            if not isinstance(account, dict):
                continue
            entitlement = account.get("entitlement") or {}
            plan = (
                entitlement.get("subscription_plan")
                if isinstance(entitlement, dict)
                else None
            )
            if plan:
                out.append({
                    "model": f"plan:{plan}",
                    "used": 0,
                    "limit": 1,
                    "reset_seconds": 0,
                    "window_seconds": 0,
                })
    return out


# ----------------------------------------------------------------------
# Real client
# ----------------------------------------------------------------------
class HttpsClient:
    """Pulls weekly usage from `/backend-api/wham/usage`.

    Args:
        access_token: Bearer token obtained from ``/api/auth/session``.
            The current wham endpoint requires it in addition to cookies.
        cookies:      list of cookie dicts (from playwright or Edge DevTools).
        user_agent:   desktop UA string used to look like a real browser.
        proxy_url:    optional HTTP/HTTPS/SOCKS5 proxy URL.
    """

    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )

    def __init__(
        self,
        access_token: str | None = None,
        cookies: list[dict] | None = None,
        user_agent: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self._token = access_token
        self._cookies = cookies or []
        self._ua = user_agent or self.DEFAULT_UA
        self._proxy_url = proxy_url or ""

    def _cookie_header(self) -> str:
        return "; ".join(
            f"{c['name']}={c['value']}" for c in self._cookies if c.get("name")
        )

    def _headers(self, referer: str | None = None) -> dict[str, str]:
        h = {
            "Accept": "application/json, text/html, */*",
            "User-Agent": self._ua,
            "Origin": "https://chatgpt.com",
            "Referer": referer or "https://chatgpt.com/codex/cloud/settings/analytics",
            "Cookie": self._cookie_header(),
            "OAI-Language": "en-US",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def fetch(self):
        from .snapshot import ModelUsage, Snapshot
        # 1. Try the Codex Cloud wham endpoint first.
        status, body, _ = _http_get(
            WHAM_USAGE_URL,
            self._headers(),
            proxy_url=self._proxy_url or None,
        )
        if 200 <= status < 300:
            snap = parse_wham_usage(body)
            if snap.ok:
                return snap

        # Wham rejected us → credentials are stale. Surface that to the tray
        # so it can prompt the user to refresh.
        if status in (401, 403):
            return Snapshot(needs_relogin=True)

        if status >= 400:
            # Fallback: legacy per-model endpoint.
            status2, body2, _ = _http_get(
                ACCOUNTS_CHECK_URL,
                self._headers(referer="https://chatgpt.com/"),
                proxy_url=self._proxy_url or None,
            )
            if 200 <= status2 < 300:
                entries = _parse_accounts_check_v4(body2)
                if entries:
                    return Snapshot(
                        models=tuple(ModelUsage(
                            model=e["model"], used=e["used"], limit=e["limit"],
                            reset_seconds=e["reset_seconds"],
                            window_seconds=e["window_seconds"],
                        ) for e in entries),
                        fetched_at=time.time(),
                    )
            if status2 in (401, 403):
                return Snapshot(needs_relogin=True)
            raise RuntimeError(
                f"wham HTTP {status}, accounts/v4 HTTP {status2}: "
                f"wham body[0:200]={body[:200]!r}"
            )

        return Snapshot(error=f"wham HTTP {status}: parse failed")
