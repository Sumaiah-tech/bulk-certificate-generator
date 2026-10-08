"""ZIP of all generated PDFs plus manifest.csv describing EVERY recipient."""
import csv
import io
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Certificate, CertStatus, Job
from app.services import storage

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Stop spreadsheet formula injection: a cell like =HYPERLINK(...) must stay plain text."""
    return "'" + value if value.startswith(_FORMULA_STARTS) else value


def build_archive(session: Session, job: Job) -> str:
    """Write jobs/<id>/certificates.zip atomically; return its path relative to STORAGE_DIR."""
    certs = session.scalars(
        select(Certificate).where(Certificate.job_id == job.id).order_by(Certificate.index)
    ).all()

    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(["index", "name", "email", "status", "error", "file"])
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            filename = ""
            if cert.status == CertStatus.COMPLETED and cert.file_path:
                filename = f"{cert.index + 1:05d}_{storage.safe_filename(cert.recipient_name)}.pdf"
                zf.write(storage.resolve(cert.file_path), filename)
            writer.writerow([
                cert.index, _csv_safe(cert.recipient_name), _csv_safe(cert.recipient_email),
                cert.status.value, _csv_safe(cert.error or ""), filename,
            ])
        # utf-8-sig = BOM, so Excel opens the file as UTF-8
        zf.writestr("manifest.csv", manifest.getvalue().encode("utf-8-sig"))

    path = storage.job_dir(job.id) / "certificates.zip"
    storage.write_atomic(path, zip_buf.getvalue())
    return path.relative_to(storage.root()).as_posix()
