"""Certificate generation: PDF content, individual failures, retries, idempotency, crash recovery."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Certificate, CertStatus, Job, JobStatus, utcnow
from app.services import generation
from app.services.certificate_pdf import render_certificate
from app.services.generation import Outcome, finalize_job_if_done, process_certificate
from tests.conftest import GOOD


def certs(db, job_id):
    db.expire_all()
    return {c.recipient_name: c for c in db.scalars(select(Certificate).where(Certificate.job_id == job_id))}


def test_pdf_is_a_real_pdf_containing_recipient_details():
    pdf = render_certificate(certificate_id="abc-123", recipient_name="Ada Lovelace", event_name="PyCon",
                             issuer="DSCE", issue_date=utcnow().date(), verify_url="http://x/verify",
                             compress=False)  # uncompressed so the text is greppable
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert b"Ada Lovelace" in pdf and b"PyCon" in pdf and b"abc-123" in pdf


def test_very_long_name_and_event_still_render():
    pdf = render_certificate(certificate_id="i", recipient_name="W" * 80, event_name="E " * 70,
                             issuer="", issue_date=utcnow().date(), verify_url="http://x")
    assert pdf.startswith(b"%PDF-")


def test_generated_files_exist_on_disk(submit, db):
    job_id = submit(GOOD).json()["job_id"]
    from app.services import storage
    for c in certs(db, job_id).values():
        assert c.status == CertStatus.COMPLETED and c.attempts == 1
        assert storage.resolve(c.file_path).read_bytes().startswith(b"%PDF-")


def test_one_failing_certificate_does_not_stop_the_others(submit, db, monkeypatch):
    real = generation.render_certificate
    def flaky(**kw):
        if kw["recipient_name"] == "Bad Apple":
            raise ValueError("secret path /srv/data exploded")
        return real(**kw)
    monkeypatch.setattr(generation, "render_certificate", flaky)

    rows = [{"name": "Good One", "email": "g1@example.com"}, {"name": "Bad Apple", "email": "b@example.com"},
            {"name": "Good Two", "email": "g2@example.com"}]
    job = submit(rows).json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["progress"]["completed"] == 2 and job["progress"]["failed"] == 1
    bad = certs(db, job["job_id"])["Bad Apple"]
    assert bad.status == CertStatus.FAILED and bad.error == "generation failed (ValueError)"
    assert "/srv" not in bad.error and bad.attempts == 1  # deterministic bug: NOT retried


def test_transient_io_error_is_retried_then_succeeds(submit, db, monkeypatch):
    real, calls = generation.render_certificate, {"n": 0}
    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("disk hiccup")
        return real(**kw)
    monkeypatch.setattr(generation, "render_certificate", flaky)
    job = submit(GOOD[:1]).json()
    c = certs(db, job["job_id"])["Ada Lovelace"]
    assert job["status"] == "COMPLETED" and c.status == CertStatus.COMPLETED and c.attempts == 3


def test_persistent_io_error_fails_after_retries(submit, db, monkeypatch):
    def always(**kw): raise OSError("disk gone")
    monkeypatch.setattr(generation, "render_certificate", always)
    job = submit(GOOD[:1]).json()
    c = certs(db, job["job_id"])["Ada Lovelace"]
    assert job["status"] == "FAILED" and c.status == CertStatus.FAILED
    assert c.attempts == 4 and "disk gone" not in c.error  # 1 try + 3 retries


def _new_job(submit, db, monkeypatch):
    """A job whose certificate has NOT run yet (we block the first run)."""
    from app import api
    monkeypatch.setattr(api.dispatch_job, "delay", lambda *_: None)
    job_id = submit(GOOD[:1]).json()["job_id"]
    return job_id, certs(db, job_id)["Ada Lovelace"].id


def test_processing_a_finished_certificate_is_a_noop(submit, db):
    job_id = submit(GOOD[:1]).json()["job_id"]
    cid = certs(db, job_id)["Ada Lovelace"].id
    assert process_certificate(cid) == Outcome.SKIPPED
    assert certs(db, job_id)["Ada Lovelace"].attempts == 1  # redelivery did not redo the work


def test_fresh_processing_claim_is_respected(submit, db, monkeypatch):
    job_id, cid = _new_job(submit, db, monkeypatch)
    c = db.get(Certificate, cid); c.status, c.started_at = CertStatus.PROCESSING, utcnow(); db.commit()
    assert process_certificate(cid) == Outcome.SKIPPED  # another worker is on it


def test_stale_processing_certificate_is_recovered(submit, db, monkeypatch):
    """Worker died mid-certificate long ago -> redelivered task takes over instead of leaving it stuck."""
    job_id, cid = _new_job(submit, db, monkeypatch)
    c = db.get(Certificate, cid)
    c.status, c.started_at = CertStatus.PROCESSING, utcnow() - timedelta(hours=1); db.commit()
    assert process_certificate(cid) == Outcome.DONE
    db.expire_all()
    assert db.get(Certificate, cid).status == CertStatus.COMPLETED
    assert db.get(Job, job_id).status == JobStatus.COMPLETED


def test_finalize_runs_exactly_once(submit, db):
    job_id = submit(GOOD).json()["job_id"]
    first = db.get(Job, job_id).finished_at
    assert finalize_job_if_done(job_id) is False  # second caller: already finalized
    db.expire_all()
    assert db.get(Job, job_id).finished_at == first


def test_job_with_unfinished_certificate_is_not_finalized(submit, db, monkeypatch):
    job_id, _ = _new_job(submit, db, monkeypatch)
    assert finalize_job_if_done(job_id) is False
    assert db.get(Job, job_id).finished_at is None
