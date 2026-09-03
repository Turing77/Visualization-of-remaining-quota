"""Immutable data models for rate-limit snapshots."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ModelUsage:
    """Per-model rate-limit usage for one rolling window.

    `window_seconds` is the size of the window — 10_800 (3h) for ChatGPT Plus
    chat, 604_800 (7 days) for Codex Cloud, etc. It's informational; the only
    required fields for display are `used`, `limit`, and `reset_seconds`.

    When the upstream endpoint only exposes `used_percent` (e.g. Codex Cloud's
    `wham/usage.primary_window.used_percent`), set `used_percent` directly
    and skip the (used/limit) derivation — the tooltip will show the
    "remaining X%" line from that.
    """

    model: str            # "gpt-4", "gpt-5-6", "codex-cloud", ...
    used: int             # calls already spent
    limit: int            # cap for this window
    reset_seconds: int    # seconds until the window resets
    window_seconds: int = 10_800  # default 3h
    used_percent: float | None = None  # 0..100, from upstream when available

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is not None:
            return max(0.0, 100.0 - self.used_percent)
        return None

    @property
    def ratio(self) -> float:
        if self.limit <= 0:
            return 0.0
        return self.remaining / self.limit

    def headline(self) -> str:
        """Compact string for tray title, e.g. 'GPT-5.6 33/40 · 1h12m' or
        'codex 100/100 · 4d 12h' for weekly windows.

        We always show `used / limit` because that's what users intuitively
        understand when a quota is "almost full" or "fully used".
        """
        short = _short_name(self.model)
        return f"{short} {self.used}/{self.limit} · {_format_reset(self.reset_seconds)}"


def _short_name(model: str) -> str:
    """Map an internal model slug to a short label for the tray tooltip."""
    specific = {
        "gpt-4": "GPT-4",
        "gpt-4o": "GPT-4o",
        "gpt-4-turbo": "GPT-4T",
        "o1-preview": "o1",
        "o1-mini": "o1m",
        "o1-pro": "o1-pro",
        "o3-mini": "o3m",
        "o3": "o3",
        "o4-mini": "o4m",
        "gpt-3.5-turbo": "3.5",
        "gpt-5": "GPT-5",
        "gpt-5-mini": "GPT-5 mini",
        "gpt-5-5": "GPT-5.5",
        "gpt-5-5-mini": "GPT-5.5 mini",
        "gpt-5-6": "GPT-5.6",
        "gpt-5-3-mini": "GPT-5.3 mini",
        "gpt-5-codex": "codex",
        # Codex Cloud budget:
        "codex-cloud": "codex",
        "codex": "codex",
        "codex-reset-credit": "credit",
    }
    if model in specific:
        return specific[model]
    if model.startswith("gpt-"):
        rest = model[4:]
        rest = rest.replace("-", ".")
        return f"GPT-{rest}" if rest else "GPT"
    return model if len(model) <= 10 else model[:10]


def _format_reset(seconds: int) -> str:
    """Human-friendly reset countdown. Handles both 3h and multi-day windows."""
    seconds = max(0, int(seconds))
    if seconds <= 0:
        return "now"
    if seconds >= 86_400:
        days, rem = divmod(seconds, 86_400)
        hours = rem // 3_600
        if hours:
            return f"{days}d{hours:02d}h"
        return f"{days}d"
    h, rem = divmod(seconds, 3_600)
    m = rem // 60
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


@dataclass(frozen=True)
class Snapshot:
    """All model usages at one moment, plus error metadata.

    `needs_relogin` signals that the upstream rejected our session
    (HTTP 401/403/HTML login wall); the tray uses this to switch to the
    gray "expired" icon and surface a re-login prompt.
    """

    models: tuple[ModelUsage, ...] = field(default_factory=tuple)
    fetched_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    needs_relogin: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    def headline(self, model: str | None = None) -> str:
        """Return the headline for the given model (or worst-ratio one).

        Capped at 127 chars so the Windows tray tooltip can't reject the
        call to NIM_ADD.
        """
        if self.needs_relogin:
            text = "Codex Tray · login expired (run `python from_pasted.py`)"
        elif not self.ok or not self.models:
            text = self._error_headline()
        else:
            pick = self._pick(model)
            text = pick.headline()
        return text[:127]

    def tooltip(self) -> str:
        if self.needs_relogin:
            lines = [
                "Codex Tray",
                "─" * 20,
                "⚠  Login expired.",
                "Cookies are invalid or revoked. Re-run:",
                "    python from_pasted.py",
                "and paste a fresh cURL from Edge DevTools.",
            ]
            return "\n".join(lines)

        if not self.ok or not self.models:
            return self._error_headline()

        lines = ["Codex Tray", "─" * 20]
        for m in self.models:
            if m.reset_seconds <= 0:
                tail = ""  # counters / one-shot values, no reset window
            else:
                tail = f" (reset {_format_reset(m.reset_seconds)})"
            lines.append(f"{_short_name(m.model):10s} {m.used:>5d}/{m.limit:<5d}{tail}")

        # Tail: a "remaining X%" line for entries whose upstream gave us a
        # percentage. We only show it for entries that actually carry a
        # used_percent (e.g. Codex Cloud), not for synthetic 1/1 counters.
        pct_rows = []
        for m in self.models:
            if m.used_percent is not None:
                rem = m.remaining_percent
                if rem is not None:
                    pct_rows.append(
                        f"{_short_name(m.model):10s} remaining: {rem:.0f}%"
                    )
        if pct_rows:
            lines.append("─" * 20)
            lines.extend(pct_rows)

        return "\n".join(lines)

    def _pick(self, model: str | None) -> ModelUsage:
        if model:
            for m in self.models:
                if m.model == model:
                    return m
        # Default: worst-ratio model (least remaining quota).
        # Skip pure "credit" counters (reset_seconds==0): they have
        # ratio=0 and would otherwise overshadow the main quota in the
        # tray title. Credits stay visible in the multi-line tooltip.
        primary = [m for m in self.models if m.reset_seconds > 0]
        pool = primary if primary else list(self.models)
        return min(pool, key=lambda m: m.ratio)

    def _error_headline(self) -> str:
        if self.error is None:
            return "Codex Tray · (no data)"
        # Windows tray tooltips are capped at 128 chars; trim the error
        # gracefully so the tray doesn't refuse to register itself.
        suffix = f"Codex Tray · {self.error}"
        return suffix[:127] if len(suffix) > 127 else suffix
