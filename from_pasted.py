"""Parse a cURL copied from your daily Edge for the analytics URL.

This file is the entire post-login flow:

  Edge (F12) > Network > GET https://chatgpt.com/codex/cloud/settings/analytics
  > right-click > Copy > Copy as cURL (bash) > paste to auth.txt > run:
      python from_pasted.py --pasted-file auth.txt

What it does:
  1. Parses cookies + user-agent out of the pasted cURL.
  2. Replays the request via `urllib` against
     `https://chatgpt.com/codex/cloud/settings/analytics` to confirm the
     session is still live.
  3. Saves the full HTML to %APPDATA%\\CodexTray\\codex_analytics.html —
     this is what we'll parse for weekly usage data.
  4. Saves cookies + access-token (if found) + proxy URL into the
     DPAPI-encrypted credentials.bin used by `python run.py`.

We deliberately don't probe `/backend-api/accounts/check` or other
JSON endpoints because we know from prior dumps that the actual weekly
usage payload lives in this SSR page's `__NEXT_DATA__` JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from codex_tray import credentials as creds_mod


COOKIE_HDR_RE = re.compile(r"""(?ix)
    ^\s* ['"]? cookie ['"]? \s* [:=] \s* ['"]?
    (?P<v> [^'"]+ )
""")
UA_HDR_RE = re.compile(r"""(?ix)
    ^\s* user-agent ['"]? \s* [:=] \s* ['"]?
    (?P<v> [^'"]+ )
""")
GENERIC_HDR_RE = re.compile(r"""(?ix)
    ^\s* (?P<k> [^:='"]+ ) \s* [:=] \s* ['"]?
    (?P<v> [^'"]+ )
""")


def parse_pasted(text: str) -> dict:
    """Pull cookies + UA + origin + referer from a 'Copy as cURL (bash)' blob.

    Also accepts a JSON document of the form ``{cookies, session}`` (the
    output of the console snippet we document alongside this script).
    """
    text = text.strip()
    if not text:
        raise SystemExit("pasted file is empty")

    # ---- JSON mode ---------------------------------------------------
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception as exc:
            raise SystemExit(f"looks like JSON but doesn't parse: {exc}")
        cookies: list[tuple[str, str]] = []
        ck = data.get("cookies") or ""
        for pair in ck.split(";"):
            pair = pair.strip()
            if "=" in pair:
                n, v = pair.split("=", 1)
                cookies.append((n.strip(), v.strip()))
        if not cookies:
            raise SystemExit(
                "JSON had no cookies — make sure you pasted the snippet output "
                "verbatim, including the `cookies` and `session` fields."
            )
        sess = data.get("session") or {}
        return {
            "cookies": cookies,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
            "origin": "https://chatgpt.com",
            "referer": "https://chatgpt.com/",
            "method": "GET",
            "url_seen": None,
            "headers": {},
            "session_json": sess,
        }

    # ---- cURL mode ---------------------------------------------------
    # Stich `curl ... \\` lines into one logical line first.
    joined = text.replace("\\\n", " ").replace("\\\r\n", " ")

    cookies = []
    headers: dict[str, str] = {}
    method = "GET"
    target_url = None

    m = re.search(r"""curl\s+['"]?(?P<url>https?://[^\s'"]+)['"]?""", joined)
    if m:
        target_url = m.group("url")

    m = re.search(r"""-X\s+['"]?(?P<m>[A-Z]+)['"]?""", joined)
    if m:
        method = m.group("m").upper()

    # Strict, greedy header capture: -H ['"]KEY: VALUE['"](?=\s|\Z)
    for m in re.finditer(
        r"""-H\s+['"](?P<h>[^'"]+)['"](?=\s|\\|\Z)""",
        joined,
    ):
        hdr = m.group("h").strip()
        # Cookie header
        cm = re.match(r"""(?i)^cookie\s*:\s*(?P<v>.+)$""", hdr)
        if cm:
            for pair in cm.group("v").split(";"):
                pair = pair.strip()
                if "=" in pair:
                    n, v = pair.split("=", 1)
                    cookies.append((n.strip(), v.strip()))
            continue
        um = re.match(r"""(?i)^user-agent\s*:\s*(?P<v>.+)$""", hdr)
        if um:
            headers["user-agent"] = um.group("v")
            continue
        if ":" in hdr:
            k, v = hdr.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    # cURL also takes cookies via -b / --cookie (no header needed).
    # Chrome/Edge's Bash export commonly uses `-b '...'`.  Cookie values
    # may themselves contain double quotes (for example `_uasid="..."`),
    # so the old `[^'"]+` pattern incorrectly rejected the whole cookie
    # argument.  Respect the outer quote style instead.
    for m in re.finditer(
        r"""(?:^|\s)-[bB]\s+(?:'(?P<single>[^']*)'|"(?P<double>[^"]*)")(?=\s|\\|\Z)""",
        joined,
    ):
        cookie_blob = m.group("single") or m.group("double") or ""
        for pair in cookie_blob.split(";"):
            pair = pair.strip()
            if "=" in pair:
                n, v = pair.split("=", 1)
                cookies.append((n.strip(), v.strip()))

    if not cookies:
        raise SystemExit(
            "no `cookie:` header found in pasted text.\n"
            "  - Make sure you used 'Copy as cURL (BASH)' (not 'cmd').\n"
            "  - Or paste the JSON snippet from the docs."
        )

    ua = headers.get("user-agent") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )
    origin = headers.get("origin", "https://chatgpt.com")
    referer = headers.get("referer", "https://chatgpt.com/")

    return {
        "cookies": cookies,
        "user_agent": ua,
        "origin": origin,
        "referer": referer,
        "method": method,
        "url_seen": target_url,
        "headers": headers,
        "session_json": {},
    }


def _build_opener(proxy_url: str):
    if not proxy_url:
        return urllib.request.build_opener()
    if proxy_url.startswith(("socks5://", "socks://")):
        try:
            import socks  # type: ignore[import-not-found]  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "SOCKS5 proxy selected but PySocks is not installed. "
                "Run: pip install PySocks"
            ) from exc
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    return urllib.request.build_opener(handler)


def fetch_url(url: str, cookies, ua: str, origin: str, referer: str,
              proxy_url: str = "") -> tuple[int, str]:
    cookie_str = "; ".join(f"{n}={v}" for n, v in cookies)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "User-Agent": ua,
            "Cookie": cookie_str,
            "Origin": origin,
            "Referer": referer,
            # ChatGPT's older mPrefetchTag needs this for SSR responses.
            "OAI-Language": "en-US",
        },
    )
    opener = _build_opener(proxy_url)
    try:
        with opener.open(req, timeout=30) as resp:  # nosec
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except Exception:
            return exc.code, str(exc)


def extract_next_data(html: str) -> dict | None:
    """Pull `__NEXT_DATA__` JSON out of a Next.js SSR page."""
    m = re.search(
        r"""<script\s+id=["']__NEXT_DATA__["']\s+type=["']application/json["']\s*>\s*(?P<v>.+?)\s*</script>""",
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group("v"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pasted-file", required=True,
                    help="text file that contains the cURL you copied")
    ap.add_argument("--analytics-url",
                    default="https://chatgpt.com/codex/cloud/settings/analytics",
                    help="URL to fetch (default: %(default)s)")
    ap.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY")
                                 or os.environ.get("HTTP_PROXY")
                                 or "http://127.0.0.1:7890",
                    help="HTTP proxy used for follow-up requests "
                         "(default: %(default)s). Set to '' to disable.")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Just parse + save credentials; skip the GET.")
    args = ap.parse_args()

    text = Path(args.pasted_file).read_text(encoding="utf-8")
    parsed = parse_pasted(text)

    print(f"[ok] parsed {len(parsed['cookies'])} cookies:")
    for n, _ in parsed["cookies"]:
        print(f"       - {n}")
    print(f"[ok] user_agent: {parsed['user_agent'][:60]}...")

    analytics_html = None
    analytics_status = None

    if not args.no_fetch:
        print(f"\n[step] GET {args.analytics_url}")
        analytics_status, analytics_html = fetch_url(
            args.analytics_url,
            parsed["cookies"],
            parsed["user_agent"],
            parsed["origin"],
            parsed["referer"],
            proxy_url=args.proxy,
        )
        print(f"[ok] HTTP {analytics_status}, {len(analytics_html or '')} bytes")

        if analytics_status == 200:
            # Save full HTML for offline inspection.
            out = Path(os.environ["APPDATA"]) / "CodexTray" / "codex_analytics.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(analytics_html, encoding="utf-8")
            print(f"[ok] saved HTML -> {out}")

            next_data = extract_next_data(analytics_html)
            if next_data:
                print(f"[ok] __NEXT_DATA__ found, {len(json.dumps(next_data))} chars")
            else:
                print("[warn] no __NEXT_DATA__ block detected in HTML — "
                      "the page may not be a Next.js SSR response.")
        else:
            print(f"[warn] analytics returned HTTP {analytics_status}; "
                  "session might be stale, redo the cURL.")

    # Probe /api/auth/session to capture accessToken (optional).
    status, body = fetch_url(
        "https://chatgpt.com/api/auth/session",
        parsed["cookies"], parsed["user_agent"],
        parsed["origin"], parsed["referer"],
        proxy_url=args.proxy,
    )
    access_token = None
    user_email = None
    user_id = None
    try:
        parsed_body = json.loads(body)
        if isinstance(parsed_body, dict):
            access_token = parsed_body.get("accessToken") or parsed_body.get("access_token")
            u = parsed_body.get("user")
            if isinstance(u, dict):
                user_email = u.get("email")
                user_id = u.get("id")
    except Exception:
        pass
    print(f"\n[step] /api/auth/session -> HTTP {status}")
    if user_email or user_id:
        print(f"       user.email    = {user_email}")
        print(f"       user.id       = {user_id}")
        if access_token:
            print(f"       accessToken*  = {access_token[:30]}...")
    else:
        print(f"       body[0:200]   = {body[:200]!r}")

    # Save credentials regardless — at minimum the cookies + UA + proxy URL.
    cookies_for_storage = [
        {"name": n, "value": v, "domain": ".chatgpt.com", "path": "/",
         "secure": True, "httpOnly": False, "sameSite": "Lax"}
        for n, v in parsed["cookies"]
    ]
    payload = {
        "generated_at": __import__("time").time(),
        "cookies": cookies_for_storage,
        "access_token": access_token,
        "user_agent": parsed["user_agent"],
        "proxy_url": args.proxy or "",
        "session_excerpt_present": bool(user_id),
        "session_user": {"email": user_email, "id": user_id} if user_id else None,
        "pasted_url": parsed.get("url_seen"),
        "analytics_status": analytics_status,
        "analytics_saved_size": len(analytics_html or ""),
        "next_data_present": analytics_html is not None
                            and extract_next_data(analytics_html) is not None,
    }
    creds_mod.save(payload)
    print(f"\n[ok] encrypted credentials written to {creds_mod.CRED_PATH}")


if __name__ == "__main__":
    main()
