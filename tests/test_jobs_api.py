"""Creating a job, request validation, auth, job status/progress."""
from app.models import Job, JobStatus, utcnow
from tests.conftest import GOOD, make_payload


def test_create_job_returns_202_with_location_and_progress(submit):
    r = submit(GOOD)
    assert r.status_code == 202
    body = r.json()
    assert r.headers["location"] == f"/api/v1/jobs/{body['job_id']}"
    assert body["progress"]["total"] == 2
    assert body["certificates_url"].endswith("/certificates")


def test_job_completes_and_reports_progress(submit, client, auth):
    job_id = submit(GOOD).json()["job_id"]
    body = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert body["status"] == "COMPLETED"
    assert body["progress"] == {"total": 2, "completed": 2, "failed": 0, "invalid": 0, "duplicate": 0,
                                "pending": 0, "processing": 0, "percent": 100.0}
    assert body["archive_url"] == f"/api/v1/jobs/{job_id}/download"
    assert body["finished_at"] is not None


def test_mixed_batch_gives_completed_with_errors(submit, client, auth):
    rows = GOOD + [{"name": "No Mail", "email": "oops"},
                   {"name": "Ada Again", "email": "ada@example.com"}]
    job = submit(rows).json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    p = job["progress"]
    assert (p["completed"], p["invalid"], p["duplicate"], p["total"]) == (2, 1, 1, 4)
    assert p["percent"] == 100.0


def test_all_invalid_job_is_failed_and_has_no_archive(submit):
    job = submit([{"name": "", "email": "x"}, {"name": "B", "email": None}]).json()
    assert job["status"] == "FAILED" and job["archive_url"] is None
    assert job["progress"]["invalid"] == 2


def test_unknown_job_is_404(client, auth):
    assert client.get("/api/v1/jobs/does-not-exist", headers=auth).status_code == 404


# ---- request-level validation -> 422 ------------------------------------------------------

def test_empty_recipient_list_is_422(submit):
    assert submit([]).status_code == 422


def test_missing_event_name_is_422(client, auth):
    body = make_payload(GOOD); del body["event_name"]
    assert client.post("/api/v1/jobs", json=body, headers=auth).status_code == 422


def test_blank_event_name_is_422(submit):
    assert submit(GOOD, event_name="   ").status_code == 422


def test_bad_date_is_422(submit):
    assert submit(GOOD, issue_date="not-a-date").status_code == 422


def test_non_latin_event_name_is_422(submit):
    assert submit(GOOD, event_name="ಕನ್ನಡ").status_code == 422


def test_too_many_recipients_is_422(submit, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_recipients_per_request", 1)
    assert submit(GOOD).status_code == 422


def test_issue_date_defaults_to_today(client, auth):
    body = make_payload(GOOD); del body["issue_date"]
    assert client.post("/api/v1/jobs", json=body, headers=auth).status_code == 202


# ---- auth ---------------------------------------------------------------------------------

def test_missing_key_is_401(client):
    assert client.post("/api/v1/jobs", json=make_payload(GOOD)).status_code == 401


def test_wrong_key_is_401(client):
    r = client.get("/api/v1/jobs/x", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_non_ascii_key_is_401_not_500(client):
    r = client.get("/api/v1/jobs/x", headers={"X-API-Key": "ключ".encode("utf-8")})
    assert r.status_code == 401


def test_second_configured_key_works(client):
    r = client.get("/api/v1/jobs/x", headers={"X-API-Key": "second-key"})
    assert r.status_code == 404  # authenticated, job just doesn't exist


def test_health_needs_no_key(client):
    assert client.get("/health").json() == {"status": "ok"}


# ---- queue failure ------------------------------------------------------------------------

def test_broker_down_returns_503_and_fails_the_job(submit, client, auth, monkeypatch):
    from app import api
    def boom(*a, **k): raise ConnectionError("redis down")
    monkeypatch.setattr(api.dispatch_job, "delay", boom)
    r = submit(GOOD)
    assert r.status_code == 503
    job_id = r.json()["detail"]["job_id"]
    body = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert body["status"] == "FAILED" and body["progress"]["failed"] == 2


# ---- listing ------------------------------------------------------------------------------

def test_certificates_listing_filter_and_pagination(submit, client, auth):
    rows = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(5)]
    rows.append({"name": "", "email": "bad"})
    job_id = submit(rows).json()["job_id"]
    base = f"/api/v1/jobs/{job_id}/certificates"
    page = client.get(base, params={"limit": 2, "offset": 1}, headers=auth).json()
    assert page["total"] == 6 and [i["index"] for i in page["items"]] == [1, 2]
    invalid = client.get(base, params={"status": "INVALID"}, headers=auth).json()
    assert invalid["total"] == 1 and invalid["items"][0]["download_url"] is None
    assert client.get(base, params={"limit": 501}, headers=auth).status_code == 422


def test_progress_while_job_is_still_running(client, auth, db):
    """A job nobody has worked on yet reports PENDING and 0% (simulates 'worker not started')."""
    import uuid
    from app.models import Certificate, CertStatus
    job = Job(id=str(uuid.uuid4()), event_name="E", issuer="", issue_date=utcnow().date(), total=2,
              status=JobStatus.PENDING)
    db.add(job); db.flush()
    db.add_all([Certificate(job_id=job.id, index=0, recipient_name="A", recipient_email="a@example.com"),
                Certificate(job_id=job.id, index=1, recipient_name="B", recipient_email="b@example.com",
                            status=CertStatus.INVALID, error="x")])
    db.commit()
    body = client.get(f"/api/v1/jobs/{job.id}", headers=auth).json()
    assert body["status"] == "PENDING" and body["progress"]["pending"] == 1
    assert body["progress"]["percent"] == 50.0 and body["archive_url"] is None
    assert client.get(f"/api/v1/jobs/{job.id}/download", headers=auth).status_code == 409
