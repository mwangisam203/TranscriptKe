"""Persist worker liveness without storing addresses, tokens or provider responses."""

from contextlib import contextmanager

from app.models.operations import WorkerRun
from app.services.orders import utcnow


@contextmanager
def tracked_run(session_factory, worker):
    with session_factory() as db:
        row = WorkerRun(worker=worker)
        db.add(row)
        db.commit()
        identifier = row.id
    counts = {"processed": 0, "failed": 0}
    crashed = False
    try:
        yield counts
    except BaseException:
        crashed = True
        raise
    finally:
        with session_factory() as db:
            row = db.get(WorkerRun, identifier)
            row.finished_at = utcnow()
            row.processed = counts["processed"]
            row.failed = max(counts["failed"], int(crashed))
            row.status = "failed" if row.failed else "succeeded"
            db.commit()
