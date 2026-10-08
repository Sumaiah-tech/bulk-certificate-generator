"""Create a job from a request; compute progress for responses."""
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.models import TERMINAL, Certificate, CertStatus, Job, JobStatus, new_id, utcnow
from app.schemas import CertificateOut, JobCreate, JobOut, Progress
from app.services.generation import status_counts
from app.services.validation import validate_recipients


def create_job(session: Session, payload: JobCreate) -> Job:
    """Validate every recipient, store the job + one row per recipient, commit. Does NOT enqueue."""
    checked = validate_recipients([(r.name, r.email) for r in payload.recipients])
    has_work = any(c.status == CertStatus.PENDING for c in checked)

    job = Job(
        id=new_id(), event_name=payload.event_name, issuer=payload.issuer, issue_date=payload.issue_date,
        total=len(checked),
        # nothing valid -> there is nothing to wait for: the job is born finished (FAILED)
        status=JobStatus.PENDING if has_work else JobStatus.FAILED,
        finished_at=None if has_work else utcnow(),
    )
    session.add(job)
    session.flush()  # job row must exist before certificates reference it
    now = utcnow()
    session.execute(insert(Certificate), [  # one bulk INSERT instead of thousands
        dict(
            id=new_id(), job_id=job.id, index=c.index, recipient_name=c.name, recipient_email=c.email,
            status=c.status, error=c.error, attempts=0,
            finished_at=None if c.status == CertStatus.PENDING else now,
        )
        for c in checked
    ])
    session.commit()
    return job


def mark_queue_failure(session: Session, job_id: str) -> None:
    """Redis unreachable: nothing will ever run, so settle the job instead of leaving it PENDING."""
    session.execute(
        update(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING)
        .values(status=CertStatus.FAILED, error="task queue unavailable", finished_at=utcnow())
    )
    session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.FAILED, finished_at=utcnow()))
    session.commit()


def progress(session: Session, job: Job) -> Progress:
    c = status_counts(session, job.id)
    done = sum(c[s] for s in TERMINAL)
    return Progress(
        total=job.total,
        completed=c[CertStatus.COMPLETED], failed=c[CertStatus.FAILED], invalid=c[CertStatus.INVALID],
        duplicate=c[CertStatus.DUPLICATE], pending=c[CertStatus.PENDING], processing=c[CertStatus.PROCESSING],
        percent=round(100 * done / job.total, 1) if job.total else 100.0,
    )


def job_out(session: Session, job: Job) -> JobOut:
    prog = progress(session, job)
    return JobOut(
        job_id=job.id, status=job.status, event_name=job.event_name, issuer=job.issuer,
        issue_date=job.issue_date, created_at=job.created_at, finished_at=job.finished_at,
        progress=prog,
        certificates_url=f"/api/v1/jobs/{job.id}/certificates",
        archive_url=f"/api/v1/jobs/{job.id}/download" if job.finished_at and prog.completed else None,
    )


def certificate_out(cert: Certificate) -> CertificateOut:
    return CertificateOut(
        certificate_id=cert.id, index=cert.index, recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email, status=cert.status, error=cert.error, attempts=cert.attempts,
        download_url=f"/api/v1/certificates/{cert.id}/download" if cert.status == CertStatus.COMPLETED else None,
    )


def get_job(session: Session, job_id: str) -> Job | None:
    return session.get(Job, job_id)


def list_certificates(session: Session, job_id: str, status: CertStatus | None, limit: int, offset: int):
    from sqlalchemy import func
    cond = [Certificate.job_id == job_id] + ([Certificate.status == status] if status else [])
    total = session.scalar(select(func.count()).select_from(Certificate).where(*cond))
    rows = session.scalars(select(Certificate).where(*cond).order_by(Certificate.index).limit(limit).offset(offset)).all()
    return total, rows
