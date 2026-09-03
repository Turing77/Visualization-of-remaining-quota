"""Parser for `/backend-api/wham/usage` (Codex Cloud weekly rate limit).

Reads the JSON body and returns a Snapshot with one or two ModelUsage
entries:
  - "codex-cloud"         : primary weekly window with used/total + reset
  - "codex-reset-credit"  : remaining free resets (a quantity, 0..1)

The endpoint returns:
    {
      "user_id": ...,
      "plan_type": "plus",
      "rate_limit": {
        "allowed": false,
        "limit_reached": true,
        "primary_window": {
          "used_percent": 100,
          "limit_window_seconds": 604800,    # 7 days
          "reset_after_seconds": 343574,     # ~4 days from now
          "reset_at": 1788751045
        },
        "secondary_window": null
      },
      ...
      "rate_limit_reset_credits": { "available_count": 1, ... }
    }
"""
from __future__ import annotations

import json
import time

from .snapshot import ModelUsage, Snapshot


def parse_wham_usage(body: str) -> Snapshot:
    try:
        data = json.loads(body)
    except Exception as exc:
        return Snapshot(error=f"wham: bad JSON: {exc}")

    if not isinstance(data, dict):
        return Snapshot(error="wham: response not an object")

    rl = data.get("rate_limit") or {}
    if not isinstance(rl, dict):
        return Snapshot(error="wham: rate_limit missing")

    primary = rl.get("primary_window") or {}
    if not isinstance(primary, dict):
        primary = {}

    limit_window = int(primary.get("limit_window_seconds") or 604_800)
    reset_after = int(primary.get("reset_after_seconds") or 0)
    used_percent = float(primary.get("used_percent") or 0.0)

    pct_model = ModelUsage(
        model="codex-cloud",
        used=int(round(used_percent)),
        limit=100,
        reset_seconds=reset_after,
        window_seconds=limit_window,
        used_percent=used_percent,
    )
    models = [pct_model]

    rrc = data.get("rate_limit_reset_credits") or {}
    if isinstance(rrc, dict):
        avail = int(rrc.get("available_count") or 0)
        models.append(ModelUsage(
            model="codex-reset-credit",
            used=max(0, 1 - avail),
            limit=1,
            reset_seconds=0,
            window_seconds=86_400 * 30,
        ))

    return Snapshot(models=tuple(models), fetched_at=time.time())
