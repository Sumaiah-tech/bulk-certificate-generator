"""HTTP routes. Thin: validation/DB/generation logic lives in app/services."""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Certificate, CertStatus, Job
from app.schemas import CertificateList, JobCreate, JobOut, VerifyOut
from app.security import require_api_key
from app.services import jobs, storage
from app.services.generation import ensure_archive
from app.tasks import dispatch_job

log = logging.getLogger(__name__)

protected = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
public = APIRouter(prefix="/api/v1")

DB = Annotated[Session, Depends(get_db)]


def _job_or_404(db: Session, job_id: str) -> Job:
    job = jobs.get_job(db, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def _cert_or_404(db: Session, certificate_id: str) -> Certificate:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, "certificate not found")
    return cert


@protected.post("/jobs", status_code=202, response_model=JobOut, summary="Submit a bulk generation job")
def create_job(payload: JobCreate, response: Response, db: DB):
    limit = get_settings().max_recipients_per_request
    if len(payload.recipients) > limit:
        raise HTTPException(422, f"too many recipients ({len(payload.recipients)}); the limit is {limit} per request")

    job = jobs.create_job(db, payload)  # validated + stored + committed BEFORE we enqueue
    job_id = job.id
    if job.finished_at is None:  # (a job with zero valid recipients is already settled)
        try:
            dispatch_job.delay(job_id)
        except Exception:  # noqa: BLE001 - broker down
            log.exception("could not enqueue job %s", job_id)
            jobs.mark_queue_failure(db, job_id)
            raise HTTPException(503, {"message": "task queue unavailable", "job_id": job_id})

    db.expire_all()  # in eager/lite mode the work already happened in another session: re-read
    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return jobs.job_out(db, db.get(Job, job_id))


@protected.get("/jobs/{job_id}", response_model=JobOut, summary="Job status and progress")
def get_job(job_id: str, db: DB):
    return jobs.job_out(db, _job_or_404(db, job_id))


@protected.get("/jobs/{job_id}/certificates", response_model=CertificateList,
               summary="Per-recipient results (paginated, filterable by status)")
def list_certificates(
    job_id: str, db: DB,
    status: CertStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _job_or_404(db, job_id)
    total, rows = jobs.list_certificates(db, job_id, status, limit, offset)
    return CertificateList(total=total, limit=limit, offset=offset, items=[jobs.certificate_out(c) for c in rows])


@protected.get("/jobs/{job_id}/download", summary="ZIP of all PDFs + manifest.csv")
def download_job(job_id: str, db: DB):
    job = _job_or_404(db, job_id)
    if job.finished_at is None:
        raise HTTPException(409, "job is still running")
    if not jobs.progress(db, job).completed:
        raise HTTPException(404, "no certificates were generated for this job")
    path = storage.resolve(ensure_archive(db, job))
    return FileResponse(path, media_type="application/zip", filename=f"certificates-{job.id[:8]}.zip")


@protected.get("/certificates/{certificate_id}/download", summary="One certificate PDF")
def download_certificate(certificate_id: str, db: DB):
    cert = _cert_or_404(db, certificate_id)
    if cert.status != CertStatus.COMPLETED or not cert.file_path:
        raise HTTPException(409, f"certificate is not generated (status: {cert.status.value})")
    return FileResponse(storage.resolve(cert.file_path), media_type="application/pdf",
                        filename=f"{storage.safe_filename(cert.recipient_name)}.pdf")


@public.get("/certificates/{certificate_id}/verify", response_model=VerifyOut,
            summary="PUBLIC: what the QR code opens. Never returns the email address")
def verify_certificate(certificate_id: str, db: DB):
    cert = db.get(Certificate, certificate_id)
    if cert is None or cert.status != CertStatus.COMPLETED:
        raise HTTPException(404, "certificate not found")
    job = db.get(Job, cert.job_id)
    return VerifyOut(valid=True, certificate_id=cert.id, recipient_name=cert.recipient_name,
                     event_name=job.event_name, issuer=job.issuer, issue_date=job.issue_date)
