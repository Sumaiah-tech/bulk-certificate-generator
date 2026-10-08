"""Retrieving certificates: single PDF, ZIP + manifest, public verification."""
import csv
import io
import zipfile

from tests.conftest import GOOD


def first_cert(client, auth, job_id, status="COMPLETED"):
    items = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": status}, headers=auth).json()["items"]
    return items[0]


def test_download_single_pdf(submit, client, auth):
    job_id = submit(GOOD).json()["job_id"]
    cert = first_cert(client, auth, job_id)
    r = client.get(cert["download_url"], headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-") and "Ada_Lovelace.pdf" in r.headers["content-disposition"]


def test_download_requires_api_key(submit, client, auth):
    job_id = submit(GOOD).json()["job_id"]
    url = first_cert(client, auth, job_id)["download_url"]
    assert client.get(url).status_code == 401


def test_invalid_certificate_cannot_be_downloaded(submit, client, auth):
    job_id = submit(GOOD + [{"name": "", "email": "bad"}]).json()["job_id"]
    cert = first_cert(client, auth, job_id, "INVALID")
    assert client.get(f"/api/v1/certificates/{cert['certificate_id']}/download", headers=auth).status_code == 409


def test_unknown_certificate_is_404(client, auth):
    assert client.get("/api/v1/certificates/nope/download", headers=auth).status_code == 404


def test_zip_contains_pdfs_and_manifest_for_every_recipient(submit, client, auth):
    rows = GOOD + [{"name": "Bad Mail", "email": "oops"}, {"name": "Dup", "email": "ada@example.com"}]
    job_id = submit(rows).json()["job_id"]
    r = client.get(f"/api/v1/jobs/{job_id}/download", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    pdfs = [n for n in zf.namelist() if n.endswith(".pdf")]
    assert sorted(pdfs) == ["00001_Ada_Lovelace.pdf", "00002_Alan_Turing.pdf"]
    manifest = zf.read("manifest.csv")
    assert manifest.startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8
    rows_out = list(csv.DictReader(io.StringIO(manifest.decode("utf-8-sig"))))
    assert [r["status"] for r in rows_out] == ["COMPLETED", "COMPLETED", "INVALID", "DUPLICATE"]
    assert "email is invalid" in rows_out[2]["error"] and rows_out[2]["file"] == ""


def test_manifest_neutralises_formula_injection(submit, client, auth):
    job_id = submit([{"name": "=HYPERLINK(\"http://evil\")", "email": "e@example.com"}]).json()["job_id"]
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/jobs/{job_id}/download", headers=auth).content))
    row = next(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode("utf-8-sig"))))
    assert row["name"].startswith("'=")


def test_zip_of_job_with_no_certificates_is_404(submit, client, auth):
    job_id = submit([{"name": "", "email": "x"}]).json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}/download", headers=auth).status_code == 404


def test_zip_is_rebuilt_if_missing(submit, client, auth):
    from app.database import SessionLocal
    from app.models import Job
    from app.services import storage
    job_id = submit(GOOD).json()["job_id"]
    with SessionLocal() as s:
        storage.resolve(s.get(Job, job_id).archive_path).unlink()
    assert client.get(f"/api/v1/jobs/{job_id}/download", headers=auth).status_code == 200


def test_public_verify_endpoint_hides_email(submit, client, auth):
    job_id = submit(GOOD).json()["job_id"]
    cid = first_cert(client, auth, job_id)["certificate_id"]
    r = client.get(f"/api/v1/certificates/{cid}/verify")  # no API key
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] and body["recipient_name"] == "Ada Lovelace" and body["event_name"] == "Python Bootcamp 2026"
    assert "email" not in r.text and "example.com" not in r.text


def test_verify_unknown_or_ungenerated_is_404(submit, client, auth):
    job_id = submit(GOOD + [{"name": "", "email": "x"}]).json()["job_id"]
    bad = first_cert(client, auth, job_id, "INVALID")["certificate_id"]
    assert client.get(f"/api/v1/certificates/{bad}/verify").status_code == 404
    assert client.get("/api/v1/certificates/nope/verify").status_code == 404


def test_storage_refuses_path_traversal():
    import pytest
    from app.services import storage
    with pytest.raises(ValueError):
        storage.resolve("../../etc/passwd")


def test_safe_filename():
    from app.services.storage import safe_filename
    assert safe_filename("../../Ada/Love:lace") == "Ada_Love_lace"
    assert safe_filename("") == "certificate"
