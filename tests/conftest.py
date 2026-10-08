"""Test setup. Everything is forced to a throw-away SQLite file + inline ("eager") Celery,
so the tests can NEVER touch your real PostgreSQL/Redis, and need neither."""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="certgen-tests-")
os.environ.update(  # assignment, not setdefault: an existing real .env must not leak in
    DATABASE_URL=f"sqlite:///{_TMP}/test.db",
    STORAGE_DIR=f"{_TMP}/storage",
    CELERY_EAGER="true",
    API_KEYS="test-key,second-key",
    PUBLIC_BASE_URL="http://testserver",
    RETRY_BACKOFF_SECONDS="0",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

assert engine.url.get_backend_name() == "sqlite", "tests must never run against a real database"


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(f"{_TMP}/storage", ignore_errors=True)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth():
    return {"X-API-Key": "test-key"}


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


def make_payload(recipients, **overrides):
    body = {"event_name": "Python Bootcamp 2026", "issuer": "DSCE Coding Club",
            "issue_date": "2026-10-08", "recipients": recipients}
    body.update(overrides)
    return body


@pytest.fixture
def submit(client, auth):
    def _submit(recipients, **overrides):
        return client.post("/api/v1/jobs", json=make_payload(recipients, **overrides), headers=auth)
    return _submit


GOOD = [{"name": "Ada Lovelace", "email": "ada@example.com"},
        {"name": "Alan Turing", "email": "alan@example.com"}]
