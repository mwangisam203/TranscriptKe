"""Small scheduled tasks recover outstanding work from the database."""

from redis import Redis
from redis.exceptions import LockNotOwnedError, RedisError

from app.celery_app import celery_app
from app.core.config import settings

# Longer than Celery's hard execution limit, shorter than Redis redelivery.
JOB_LOCK_SECONDS = 300


def job_redis():
    return Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=3
    )


def broker_ready():
    try:
        with job_redis() as client:
            return bool(client.ping())
    except (RedisError, ValueError):
        return False


def run_exclusive(name, runner):
    with job_redis() as client:
        lock = client.lock(f"transcriptske:job-lock:{name}", timeout=JOB_LOCK_SECONDS)
        if not lock.acquire(blocking=False):
            return {"skipped": "already_running"}
        try:
            return runner(settings.JOB_BATCH_SIZE)
        except Exception:
            # Provider/SQL exceptions can contain private response data or parameters.
            # The existing runner records failed health in PostgreSQL.
            raise RuntimeError(
                f"The {name} background job failed; check worker health."
            ) from None
        finally:
            try:
                lock.release()
            except (LockNotOwnedError, RedisError):
                # Never delete another worker's lock after expiry or a reconnect.
                pass


@celery_app.task(name="transcriptske.reconcile_payments")
def reconcile_payments():
    from app.payment_worker import run

    return run_exclusive("payments", run)


@celery_app.task(name="transcriptske.send_document_notifications")
def send_document_notifications():
    from app.delivery_worker import run

    return run_exclusive("deliveries", run)


@celery_app.task(name="transcriptske.send_payment_receipts")
def send_payment_receipts():
    from app.receipt_worker import run

    return run_exclusive("receipts", run)
