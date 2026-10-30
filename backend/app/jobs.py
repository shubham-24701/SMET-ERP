"""Database-backed job queue and the worker loop (notifications, reminders, clean-ups).

The worker runs as a thread inside the API process by default (one command to start).
`uv run python -m app.jobs` runs it as its own process instead; then set RUN_WORKER_IN_API=false.
Several workers can run at once: a job is claimed with FOR UPDATE SKIP LOCKED.
"""

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logs import redact
from app.models import AuthSession, Job, utcnow

log = logging.getLogger("smet.worker")
LEASE = timedelta(minutes=5)  # a running job whose lease expires was abandoned by a dead worker
HANDLERS: dict[str, Callable[[Session, dict], None]] = {}
PERIODIC: list[tuple[Callable[[Session], None], int]] = []


def handler(kind: str):
    def register(fn):
        HANDLERS[kind] = fn
        return fn

    return register


def periodic(every_s: int):
    def register(fn):
        PERIODIC.append((fn, every_s))
        return fn

    return register


def enqueue(db: Session, kind: str, payload: dict, *, key: str | None = None,
            run_at: datetime | None = None, max_attempts: int = 5) -> None:  # fmt: skip
    """Add a job in the caller's transaction. A job with the same key is only ever added once."""
    if key and db.scalar(select(Job.id).where(Job.key == key)):
        return
    db.add(
        Job(
            kind=kind,
            payload=payload,
            key=key,
            run_at=run_at or utcnow(),
            max_attempts=max_attempts,
        )
    )


def claim(db: Session) -> Job | None:
    now = utcnow()
    job = db.scalar(
        select(Job)
        .where(
            ((Job.status == "pending") & (Job.run_at <= now))
            | ((Job.status == "running") & (Job.lease_until < now))
        )
        .order_by(Job.run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.rollback()
        return None
    job.status, job.lease_until, job.attempts = "running", now + LEASE, job.attempts + 1
    db.commit()
    return job


def run_one(db: Session) -> bool:
    job = claim(db)
    if job is None:
        return False
    try:
        HANDLERS[job.kind](db, job.payload)
        job = db.get(Job, job.id)
        job.status, job.lease_until = "done", None
    except Exception as e:  # any failure is retried with backoff, then dead-lettered
        db.rollback()
        log.exception("Job %s (%s) failed", job.id, job.kind)
        job = db.get(Job, job.id)
        job.last_error, job.lease_until = redact(repr(e))[:300], None
        if job.attempts >= job.max_attempts:
            job.status = "dead"  # kept for review (System health)
        else:
            job.status = "pending"
            job.run_at = utcnow() + timedelta(seconds=min(30 * 2**job.attempts, 3600))
    db.commit()
    return True


def run_due_jobs(limit: int = 100) -> int:
    count = 0
    with SessionLocal() as db:
        while count < limit and run_one(db):
            count += 1
    return count


def summary(db: Session) -> dict:
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    dead = db.scalars(
        select(Job).where(Job.status == "dead").order_by(Job.created_at.desc()).limit(20)
    )
    return {s: counts.get(s, 0) for s in ("pending", "running", "done", "dead")} | {
        "dead_jobs": [
            {"id": j.id, "kind": j.kind, "attempts": j.attempts, "last_error": j.last_error}
            for j in dead
        ]
    }


@periodic(3600)
def purge_expired(db: Session) -> None:
    """Expired sessions and jobs finished over a week ago (failed ones stay for review)."""
    now = utcnow()
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    db.execute(delete(Job).where(Job.status == "done", Job.created_at < now - timedelta(days=7)))
    db.commit()


def worker_loop(stop: threading.Event) -> None:
    last_run: dict[Callable, float] = {}
    log.info("Worker started")
    while not stop.is_set():
        did_work = False
        try:
            did_work = run_due_jobs(limit=20) > 0
            now = time.monotonic()
            for fn, every in PERIODIC:
                if now - last_run.get(fn, 0) >= every:
                    last_run[fn] = now
                    try:  # one failing task never blocks the others
                        with SessionLocal() as db:
                            fn(db)
                    except Exception:
                        log.exception("Periodic task %s failed", fn.__name__)
        except Exception:  # never let one bad iteration (database restarting) kill the worker
            log.exception("Worker loop error")
        if not did_work:
            stop.wait(1.0)
    log.info("Worker stopped")


def start_in_background() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=worker_loop, args=(stop,), name="worker", daemon=True).start()
    return stop


if __name__ == "__main__":
    import app.main  # noqa: F401  (registers every handler)
    from app.logs import setup_logging

    setup_logging()
    worker_loop(threading.Event())
