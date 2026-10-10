"""Redis carries job signals; PostgreSQL remains the authoritative work queue."""

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings

celery_app = Celery("transcriptske", broker=settings.REDIS_URL, include=["app.tasks"])
celery_app.conf.update(
    task_default_queue="transcriptske.jobs",
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=240,
    task_time_limit=270,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,
    broker_transport_options={
        "visibility_timeout": 360,
        "global_keyprefix": "transcriptske:",
        "socket_connect_timeout": 3,
        "socket_timeout": 5,
    },
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "payment-receipts": {
            "task": "transcriptske.send_payment_receipts",
            "schedule": 10,
            "options": {"expires": 20},
        },
        "payment-reconciliation": {
            "task": "transcriptske.reconcile_payments",
            "schedule": settings.PAYMENT_JOB_INTERVAL_SECONDS,
            "options": {"expires": settings.PAYMENT_JOB_INTERVAL_SECONDS * 2},
        },
        "document-notifications": {
            "task": "transcriptske.send_document_notifications",
            "schedule": settings.DELIVERY_JOB_INTERVAL_SECONDS,
            "options": {"expires": settings.DELIVERY_JOB_INTERVAL_SECONDS * 2},
        },
    }
    if settings.BACKGROUND_JOBS_ENABLED
    else {},
)


@worker_process_init.connect
def reset_database_pool(**kwargs):
    # Forked worker processes must not share inherited database connections.
    from app.db.session import engine

    engine.dispose(close=False)
