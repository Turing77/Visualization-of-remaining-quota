"""Headless smoke test — does NOT open a tray window."""
from __future__ import annotations

import io
import py_compile
import pathlib

from codex_tray.client import MockClient
from codex_tray.icon import render
from codex_tray.poller import Poller
from codex_tray.snapshot import ModelUsage, Snapshot


def test_parse_pasted_chrome_bash_curl() -> None:
    """Chrome's Bash export uses --url and -b, not a Cookie header."""
    from from_pasted import parse_pasted

    pasted = r'''curl --url 'https://chatgpt.com/codex/cloud/settings/analytics' \
      -b 'first=one; quoted="value"; session=three' \
      -H 'user-agent: Test Browser/1.0' '''
    parsed = parse_pasted(pasted)
    assert parsed["url_seen"] == (
        "https://chatgpt.com/codex/cloud/settings/analytics"
    )
    assert parsed["cookies"] == [
        ("first", "one"),
        ("quoted", '"value"'),
        ("session", "three"),
    ]
    assert parsed["user_agent"] == "Test Browser/1.0"

    long_form = parse_pasted(
        "curl.exe --url=https://chatgpt.com/ --cookie='token=abc'"
    )
    assert long_form["url_seen"] == "https://chatgpt.com/"
    assert long_form["cookies"] == [("token", "abc")]
    print("[ok] Chrome/Edge Bash cURL supports --url and -b/--cookie")


def test_extract_session_details() -> None:
    from from_pasted import extract_session_details

    body = '''{
      "accessToken": "test-access-token",
      "user": {"email": "person@example.com", "id": "user-123"}
    }'''
    assert extract_session_details(body) == (
        "test-access-token",
        "person@example.com",
        "user-123",
    )
    assert extract_session_details("not JSON") == (None, None, None)
    print("[ok] auth/session access token extraction")


def test_parse_wham_real_response() -> None:
    """Real `/backend-api/wham/usage` response shape, captured live."""
    body = """{
      "user_id": "<user-id>",
      "plan_type": "plus",
      "rate_limit": {
        "limit_reached": true,
        "primary_window": {
          "used_percent": 100,
          "limit_window_seconds": 604800,
          "reset_after_seconds": 343574,
          "reset_at": 1788751045
        },
        "secondary_window": null
      },
      "rate_limit_reset_credits": { "available_count": 1 }
    }"""
    from codex_tray.parse_wham import parse_wham_usage
    snap = parse_wham_usage(body)
    assert snap.ok, snap.error
    assert len(snap.models) == 2
    primary = snap.models[0]
    assert primary.model == "codex-cloud"
    assert primary.used == 100
    assert primary.limit == 100
    assert primary.reset_seconds == 343574
    assert primary.window_seconds == 604_800
    assert primary.used_percent == 100.0
    assert primary.remaining_percent == 0.0
    credit = snap.models[1]
    assert credit.model == "codex-reset-credit"
    assert credit.used == 0  # 1 available = 0 used
    tip = snap.tooltip()
    assert "remaining: 0%" in tip
    print("[ok] parse_wham_usage on real payload (with used_percent + tooltip)")


def test_snapshot_with_partial_usage() -> None:
    """Verify `remaining: 28%` appears when used_percent < 100."""
    from codex_tray.parse_wham import parse_wham_usage
    body = """{
      "rate_limit": {
        "primary_window": {
          "used_percent": 72,
          "limit_window_seconds": 604800,
          "reset_after_seconds": 100000,
          "reset_at": 1788751045
        }
      },
      "rate_limit_reset_credits": { "available_count": 0 }
    }"""
    snap = parse_wham_usage(body)
    primary = snap.models[0]
    assert primary.used == 72
    assert primary.remaining_percent == 28.0
    tip = snap.tooltip()
    assert "remaining: 28%" in tip, tip
    print("[ok] partial usage renders 'remaining: 28%'")


def test_needs_relogin() -> None:
    """Tray UI should switch to 'expired' state when session is stale."""
    from codex_tray.snapshot import Snapshot
    from codex_tray import icon as icon_mod
    snap = Snapshot(error="401", needs_relogin=True)
    img = icon_mod.render(text="?", status="expired")
    assert img.size[0] == img.size[1]
    assert "login expired" in snap.headline()
    assert "Login expired" in snap.tooltip()
    print("[ok] needs_relogin → gray icon + login-expired headline/tooltip")


def test_compile_all() -> None:
    src = pathlib.Path("codex_tray")
    files = list(src.glob("*.py"))
    assert files, "no python files"
    for f in files:
        py_compile.compile(str(f), doraise=True)
    print(f"[ok] compiled {len(files)} files")


def test_snapshot_text() -> None:
    snap = Snapshot(
        models=(
            ModelUsage(model="gpt-5-6", used=7, limit=40, reset_seconds=3725),
            ModelUsage(model="gpt-5-mini", used=12, limit=80, reset_seconds=3660),
        )
    )
    headline = snap.headline()
    assert headline.startswith("GPT-"), headline
    assert "gpt-5" not in headline, f"expected short name, got: {headline}"
    tip = snap.tooltip()
    assert "Codex Tray" in tip
    assert "7/40" in tip, tip  # used=7, limit=40 → "7/40 (reset ...)"
    print("[ok] snapshot headline:", snap.headline())
    print("[ok] snapshot tooltip:")
    for line in tip.splitlines():
        print("      ", line)


def test_mock_client() -> None:
    c = MockClient()
    snap1 = c.fetch()
    assert snap1.ok and len(snap1.models) == 1
    m = snap1.models[0]
    assert m.model == "codex-cloud" and m.limit == 500 and m.window_seconds == 604_800
    print(f"[ok] mock client model: {m.model} {m.used}/{m.limit} reset={m.reset_seconds}s")


def test_icon_render() -> None:
    # Number-driven renders at common percentages (with progress ring).
    for pct, label in [(100, "100"), (88, "88"), (60, "60"),
                       (50, "50"), (28, "28"), (19, "19"), (5, "5"), (0, "0")]:
        img = render(text=label, gauge_percent=pct)
        assert img.size[0] == img.size[1]
        buf = io.BytesIO()
        img.save(buf, "PNG")
        assert buf.tell() > 0

    # Status glyphs
    for status in ("err", "expired"):
        img = render(text="!", status=status)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        assert buf.tell() > 0

    print("[ok] icon render for 8 percent levels + 2 status glyphs")


def test_icon_colour_thresholds() -> None:
    from codex_tray.icon import GREEN, RED, YELLOW, color_for_ratio
    assert GREEN == (16, 124, 16)
    assert YELLOW == (255, 185, 0)
    assert RED == (209, 52, 56)
    assert color_for_ratio(1.00) == GREEN
    assert color_for_ratio(0.60) == GREEN
    assert color_for_ratio(0.599) == YELLOW
    assert color_for_ratio(0.20) == YELLOW
    assert color_for_ratio(0.199) == RED
    assert color_for_ratio(0.00) == RED
    print("[ok] icon colours: >=60 green, 20..<60 yellow, <20 red")


def test_dpi_icon_sizes() -> None:
    from codex_tray.icon import DPI_ICON_SIZES, render_dpi_set
    icons = render_dpi_set(text="68", gauge_percent=68)
    assert tuple(icons) == DPI_ICON_SIZES
    assert all(img.size == (size, size) for size, img in icons.items())
    assert all(img.mode == "RGBA" for img in icons.values())
    print(f"[ok] native DPI icon set: {DPI_ICON_SIZES}")


def test_poller_refresh_once() -> None:
    p = Poller(MockClient(), interval_s=999)
    snap = p.refresh_once()
    assert snap.ok
    assert p.snapshot is snap
    print("[ok] poller refresh_once works (snapshot stored)")


def test_error_snapshot() -> None:
    snap = Snapshot(error="boom")
    assert not snap.ok
    assert "boom" in snap.headline()
    print("[ok] error snapshot headline:", snap.headline())


def test_short_name() -> None:
    from codex_tray.snapshot import _short_name
    cases = {
        "gpt-4": "GPT-4",
        "gpt-4o": "GPT-4o",
        "gpt-5-6": "GPT-5.6",          # Plus-internal slug from /backend-api/models
        "gpt-5-5-mini": "GPT-5.5 mini",
        "gpt-5": "GPT-5",
        "gpt-5-codex": "codex",
        "codex-cloud": "codex",
        "o1-preview": "o1",
        "o3-mini": "o3m",
        "o4-mini": "o4m",
        "gpt-5-mini": "GPT-5 mini",
    }
    for slug, expected in cases.items():
        got = _short_name(slug)
        assert got == expected, f"{slug!r} -> {got!r}, want {expected!r}"
    print(f"[ok] _short_name cases all pass ({len(cases)} slugs)")


def test_format_reset() -> None:
    from codex_tray.snapshot import _format_reset
    cases = [
        (0, "now"),
        (60, "1m"),
        (3540, "59m"),          # <1h
        (3600, "1h00m"),
        (5 * 3600 + 30 * 60, "5h30m"),
        (86_400, "1d"),         # 1 day exact
        (4 * 86_400, "4d"),
        (4 * 86_400 + 12 * 3600, "4d12h"),
        (7 * 86_400, "7d"),
    ]
    for seconds, expected in cases:
        got = _format_reset(seconds)
        assert got == expected, f"{seconds} -> {got!r}, want {expected!r}"
    print(f"[ok] _format_reset cases all pass ({len(cases)} durations)")


def test_plus_mock_snapshot() -> None:
    from codex_tray.client import MockClient
    snap = MockClient().fetch()
    assert snap.ok
    slugs = [m.model for m in snap.models]
    assert slugs == ["codex-cloud"], f"Mock should expose Codex Cloud weekly usage, got: {slugs}"
    m = snap.models[0]
    assert m.limit == 500
    assert m.window_seconds == 604_800
    headline = snap.headline()
    assert "codex" in headline
    # Reset must be shown in days, not "h" or "m"
    assert "d" in headline, f"weekly window should render days, got: {headline}"
    print("[ok] Codex-Cloud mock:", slugs, "headline:", headline)


if __name__ == "__main__":
    test_parse_pasted_chrome_bash_curl()
    test_extract_session_details()
    test_compile_all()
    test_snapshot_text()
    test_short_name()
    test_format_reset()
    test_mock_client()
    test_plus_mock_snapshot()
    test_parse_wham_real_response()
    test_snapshot_with_partial_usage()
    test_needs_relogin()
    test_icon_render()
    test_icon_colour_thresholds()
    test_dpi_icon_sizes()
    test_poller_refresh_once()
    test_error_snapshot()
    print("\nall smoke tests passed.")
