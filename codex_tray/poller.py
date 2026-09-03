"""Background poller that updates a shared Snapshot."""
from __future__ import annotations

import logging
import threading
import time

from .client import RateLimitClient
from .snapshot import Snapshot

log = logging.getLogger(__name__)


class Poller:
    """Thread-safe wrapper that runs `client.fetch()` on an interval."""

    def __init__(self, client: RateLimitClient, interval_s: float = 60.0) -> None:
        self._client = client
        self._interval = interval_s
        self._lock = threading.Lock()
        self._snapshot: Snapshot = Snapshot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_update = None  # callable[[Snapshot], None]

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def on_update(self, callback) -> None:
        """Register a callback to be invoked whenever the snapshot is refreshed."""
        self._on_update = callback

    def refresh_once(self) -> Snapshot:
        """Synchronously fetch a single snapshot and store it."""
        snap = self._safe_fetch()
        with self._lock:
            self._snapshot = snap
        if self._on_update:
            try:
                self._on_update(snap)
            except Exception:  # don't let UI bugs kill the loop
                log.exception("on_update callback failed")
        return snap

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="codex-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- internals ----------

    def _run(self) -> None:
        # First fetch happens immediately so the UI isn't blank.
        self.refresh_once()
        while not self._stop.wait(self._interval):
            self.refresh_once()

    def _safe_fetch(self) -> Snapshot:
        try:
            return self._client.fetch()
        except NotImplementedError:
            return Snapshot(error="not-implemented")
        except Exception as exc:
            log.warning("fetch failed: %s", exc)
            return Snapshot(error=str(exc) or exc.__class__.__name__)
