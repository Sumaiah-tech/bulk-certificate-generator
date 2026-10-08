"""The heart of the system: generate ONE certificate safely, and close out the job when the last one ends.

Read this file top to bottom together with app/tasks.py -- it is the whole story.
"""
import logging
from datetime import timedelta
from enum import Enum

from sqlalchemy import and_, func, or_, select, update

from app.config import get_settings
from app.database import SessionLocal
from app.models import TERMINAL, Certificate, CertStatus, Job, JobStatus, utcnow
from app.services import archive, storage
from app.services.certificate_pdf import render_certificate

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    DONE = "done"        # PDF written
    FAILED = "failed"    # gave up, marked FAILED
    RETRY = "retry"      # transient error: caller should schedule another attempt
    SKIPPED = "skipped"  # nothing to do (already finished / being handled by someone else)


def verify_url(certificate_id: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/api/v1/certificates/{certificate_id}/verify"


def process_certificate(certificate_id: str, *, final_attempt: bool = False) -> Outcome:
    # STEP 1 - CLAIM. One atomic UPDATE decides which worker owns this certificate.
    #   Claimable = PENDING, or PROCESSING but stale (its worker presumably died).
    #   A finished certificate matches nothing -> 0 rows -> SKIPPED. That makes the task IDEMPOTENT,
    #   which is what makes `acks_late` (redelivery after a crash) safe.
    now = utcnow()
    stale_before = now - timedelta(seconds=get_settings().processing_timeout_seconds)
    with SessionLocal() as s:
        claimed = s.execute(
            update(Certificate)
            .where(
                Certificate.id == certificate_id,
                or_(
                    Certificate.status == CertStatus.PENDING,
                    and_(Certificate.status == CertStatus.PROCESSING, Certificate.started_at < stale_before),
                ),
            )
            .values(status=CertStatus.PROCESSING, started_at=now, attempts=Certificate.attempts + 1, error=None)
        )
        if claimed.rowcount != 1:
            s.rollback()
            return Outcome.SKIPPED
        cert = s.get(Certificate, certificate_id)
        job = s.get(Job, cert.job_id)
        # first certificate to start flips the job PENDING -> PROCESSING
        s.execute(update(Job).where(Job.id == job.id, Job.status == JobStatus.PENDING)
                  .values(status=JobStatus.PROCESSING))
        s.commit()
        job_id, name, event, issuer, issued = cert.job_id, cert.recipient_name, job.event_name, job.issuer, job.issue_date

    # STEP 2 - DO THE WORK (outside any DB transaction, so a slow render never holds a lock).
    try:
        pdf = render_certificate(
            certificate_id=certificate_id, recipient_name=name, event_name=event,
            issuer=issuer, issue_date=issued, verify_url=verify_url(certificate_id),
        )
        relative_path = storage.write_certificate(job_id, certificate_id, pdf)
    except OSError as exc:
        # Disk hiccup: worth retrying. Anything else is a deterministic bug: retrying is pointless.
        log.warning("transient error on %s: %s", certificate_id, exc)
        if not final_attempt:
            _set(certificate_id, status=CertStatus.PENDING, error="temporary I/O error, retrying")
            return Outcome.RETRY
        outcome = _fail(job_id, certificate_id, "I/O error after retries (OSError)")
    except Exception as exc:  # noqa: BLE001 - one bad certificate must never kill the others
        log.exception("certificate %s failed", certificate_id)
        # Only the exception CLASS goes to the client: messages can contain server paths.
        outcome = _fail(job_id, certificate_id, f"generation failed ({type(exc).__name__})")
    else:
        _set(certificate_id, status=CertStatus.COMPLETED, file_path=relative_path, error=None, finished_at=utcnow())
        outcome = Outcome.DONE

    # STEP 3 - was I the last one? (if so, build the ZIP and set the job's final status)
    finalize_job_if_done(job_id)
    return outcome


def _set(certificate_id: str, **values) -> None:
    with SessionLocal() as s:
        s.execute(update(Certificate).where(Certificate.id == certificate_id).values(**values))
        s.commit()


def _fail(job_id: str, certificate_id: str, message: str) -> Outcome:
    _set(certificate_id, status=CertStatus.FAILED, error=message, finished_at=utcnow())
    return Outcome.FAILED


def status_counts(session, job_id: str) -> dict[CertStatus, int]:
    rows = session.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    counts = {st: 0 for st in CertStatus}
    counts.update({st: n for st, n in rows})
    return counts


def finalize_job_if_done(job_id: str) -> bool:
    """Set the job's final status + build the ZIP -- exactly once, even if workers finish simultaneously.

    `SELECT ... FOR UPDATE` locks the job row (PostgreSQL), so concurrent workers queue up here.
    The first one in sees "nothing left" and finalizes; the rest see finished_at set and leave.
    """
    with SessionLocal() as s:
        job = s.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
        if job.finished_at is not None:
            return False
        counts = status_counts(s, job_id)
        if counts[CertStatus.PENDING] or counts[CertStatus.PROCESSING]:
            return False  # someone else is still working; THEY will finalize
        completed = counts[CertStatus.COMPLETED]
        if completed:
            try:
                job.archive_path = archive.build_archive(s, job)
            except Exception:  # noqa: BLE001 - not fatal: the download endpoint rebuilds it on demand
                log.exception("could not build archive for job %s", job_id)
        if completed == 0:
            job.status = JobStatus.FAILED
        elif completed == job.total:
            job.status = JobStatus.COMPLETED
        else:
            job.status = JobStatus.COMPLETED_WITH_ERRORS
        job.finished_at = utcnow()
        s.commit()
        return True


def ensure_archive(session, job: Job) -> str:
    """Return the job's ZIP path, (re)building it if the finalizer failed to."""
    if job.archive_path and storage.resolve(job.archive_path).exists():
        return job.archive_path
    job.archive_path = archive.build_archive(session, job)
    session.commit()
    return job.archive_path


__all__ = ["Outcome", "process_certificate", "finalize_job_if_done", "status_counts", "ensure_archive", "TERMINAL"]
