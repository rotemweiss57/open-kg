"""Celery transport. Tasks are thin: all durability lives in executor.py / Postgres.

`acks_late` + `reject_on_worker_lost` give at-least-once delivery; the run lease and step
idempotency keys make redelivery harmless. Beat runs the reconciler so a run committed to
Postgres is executed even if its original task message was never published or was lost.
"""
from __future__ import annotations

from celery import Celery

from ..config import get_settings

st = get_settings()
celery = Celery("openkg", broker=st.redis_url, backend=None)
celery.conf.update(
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_default_queue="openkg", broker_connection_retry_on_startup=True, timezone="UTC",
    task_time_limit=60 * 60 * 6,
    beat_schedule={
        "reconcile-runs": {"task": "openkg.reconcile", "schedule": 5.0},
        "schedule-monitors": {"task": "openkg.schedule_monitors", "schedule": 60.0},
    },
)


@celery.task(name="openkg.execute_run", bind=True, max_retries=None)
def execute_run_task(self, run_id: str) -> str:
    from .executor import execute_run
    return execute_run(run_id)


@celery.task(name="openkg.reconcile")
def reconcile() -> int:
    from .executor import due_runs, mark_dispatched, settle_control_requests
    settle_control_requests()
    ids = due_runs()
    for run_id in ids:
        execute_run_task.delay(run_id)
        mark_dispatched(run_id)
    return len(ids)


@celery.task(name="openkg.monitor_check")
def monitor_check_task(monitor_id: str) -> str | None:
    from ..domain.monitors import run_monitor_check
    return run_monitor_check(monitor_id)


@celery.task(name="openkg.schedule_monitors")
def schedule_monitors() -> int:
    from ..domain.monitors import due_monitors
    ids = due_monitors()
    for mid in ids:
        monitor_check_task.delay(mid)
    return len(ids)


def publish_run(run_id: str) -> bool:
    """Best-effort fast path. Failure is fine: the reconciler will pick the run up."""
    from .executor import mark_dispatched
    try:
        execute_run_task.apply_async(args=[run_id], retry=False)
    except Exception:  # noqa: BLE001
        return False
    mark_dispatched(run_id)
    return True
