"""In-process durable worker for zero-dependency local development (`EXECUTION_MODE=embedded`).

It uses exactly the same lease/executor/reconciler code as the Celery deployment; only the
transport differs (it polls the runs table instead of consuming a queue).
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..domain.monitors import due_monitors, run_monitor_check
from .executor import due_runs, execute_run, settle_control_requests, worker_identity

log = logging.getLogger("openkg.worker")


class EmbeddedWorker:
    def __init__(self, concurrency: int = 4, poll_interval: float = 0.4):
        self.pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="openkg-run")
        self.poll_interval = poll_interval
        self.stop_event = threading.Event()
        self.identity = worker_identity()
        self.active: set[str] = set()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.last_tick = 0.0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True, name="openkg-embedded-worker")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, run_id: str) -> None:
        try:
            execute_run(run_id, f"{self.identity}:{threading.get_ident()}")
        finally:
            with self.lock:
                self.active.discard(run_id)

    def tick(self) -> None:
        settle_control_requests()
        for run_id in due_runs(redispatch_after=0):
            with self.lock:
                if run_id in self.active:
                    continue
                self.active.add(run_id)
            self.pool.submit(self._run, run_id)

    def _loop(self) -> None:
        last_monitor = 0.0
        while not self.stop_event.wait(self.poll_interval):
            try:
                self.tick()
                if time.monotonic() - last_monitor > 20:
                    last_monitor = time.monotonic()
                    for mid in due_monitors():
                        self.pool.submit(run_monitor_check, mid)
                self.last_tick = time.time()
            except Exception:  # noqa: BLE001
                log.exception("embedded worker tick failed")
