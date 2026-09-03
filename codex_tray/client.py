"""ChatGPT / Codex Cloud rate-limit client.

`MockClient` synthesises believable Codex-Cloud weekly usage so the tray UI
can be exercised end-to-end. The real fetcher lives in `http_client.py`.
"""
from __future__ import annotations

import abc
import random
import time

from .snapshot import ModelUsage, Snapshot


class RateLimitClient(abc.ABC):
    """Abstract rate-limit client. Implementations must be thread-safe."""

    @abc.abstractmethod
    def fetch(self) -> Snapshot:
        ...


class MockClient(RateLimitClient):
    """Synthesizes believable Codex-Cloud weekly usage data."""

    def __init__(self) -> None:
        # start counting down ~4 days into a 7-day window
        self._reset_seconds = 4 * 86_400
        self._used_base = 60.0

    def fetch(self) -> Snapshot:
        # pretend 60 s elapsed since the previous call
        self._reset_seconds = max(0, self._reset_seconds - 60)
        # realistic per-call drift on the "used" counter
        self._used_base += (random.random() - 0.4) * 1.2
        used = max(0, min(500 - 5, int(self._used_base)))

        if self._reset_seconds == 0:
            # window rolled over — start a fresh weekly window
            self._reset_seconds = 4 * 86_400
            self._used_base = max(20.0, self._used_base * 0.4)
            used = int(self._used_base)

        return Snapshot(
            models=(
                ModelUsage(
                    model="codex-cloud",
                    used=used,
                    limit=500,
                    reset_seconds=self._reset_seconds,
                    window_seconds=604_800,
                ),
            ),
            fetched_at=time.time(),
        )
