"""Celery tasks. Deliberately thin: all logic lives in services/generation.py."""
import logging

from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.database import SessionLocal
from app.models import Certificate, CertStatus
from app.services.generation import Outcome, process_certificate

log = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="certgen.dispatch_job")
def dispatch_job(job_id: str) -> int:
    """Fan-out: queue one generate_certificate task per valid recipient.
    Done in a task (not in the HTTP request) so even 5,000 recipients return instantly."""
    with SessionLocal() as s:
        ids = s.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING)
            .order_by(Certificate.index)
        ).all()
    for cid in ids:
        generate_certificate.delay(cid)
    return len(ids)


@celery_app.task(bind=True, name="certgen.generate_certificate", max_retries=settings.max_retries)
def generate_certificate(self, certificate_id: str) -> str:
    final = self.request.retries >= self.max_retries
    outcome = process_certificate(certificate_id, final_attempt=final)
    if outcome is Outcome.RETRY:
        # exponential backoff: 2s, 4s, 8s ...
        raise self.retry(countdown=settings.retry_backoff_seconds * 2 ** self.request.retries)
    return outcome.value
