"""Celery configuration. The comments say WHY each reliability setting exists."""
from celery import Celery

from app.config import get_settings

s = get_settings()

celery_app = Celery("certgen", broker=s.redis_url, include=["app.tasks"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_ignore_result=True,  # results live in PostgreSQL, not in Celery's backend
    # Acknowledge the message only AFTER the task finishes: if a worker dies mid-task,
    # Redis hands the message to another worker. Safe because tasks are idempotent.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One task at a time per worker slot, so a slow certificate never hides others in a prefetch buffer.
    worker_prefetch_multiplier=1,
    # Redis redelivers unacked messages after this long. It must be LONGER than
    # processing_timeout_seconds, otherwise a redelivered task would find the certificate
    # still "freshly claimed" by the dead worker and give up.
    broker_transport_options={"visibility_timeout": s.processing_timeout_seconds * 2},
    broker_connection_retry_on_startup=True,
    # Lite mode / tests: run tasks inline in the calling process (no Redis, no worker).
    # (eager_propagates stays False: Celery's internal Retry signal must not escape in eager mode.
    #  Our task code catches its own errors and records them in the DB, so nothing is hidden.)
    task_always_eager=s.celery_eager,
)
