"""Request and response models (what the API accepts and returns)."""
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import CertStatus, JobStatus
from app.services.validation import has_control_chars, latin_problem


class RecipientIn(BaseModel):
    # Deliberately loose (Any): a bad value must become an INVALID row with a reason,
    # not a 422 that rejects the other 1,999 good recipients.
    name: Any = None
    email: Any = None


class JobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=150, examples=["Python Bootcamp 2026"])
    issuer: str = Field(default="", max_length=100, examples=["DSCE Coding Club"])
    issue_date: date = Field(default_factory=date.today, examples=["2026-10-08"])
    recipients: list[RecipientIn] = Field(min_length=1)

    @field_validator("event_name", "issuer")
    @classmethod
    def _printable(cls, v: str) -> str:
        if has_control_chars(v):
            raise ValueError("must not contain control characters")
        if problem := latin_problem(v):
            raise ValueError(problem)
        return v


def _as_utc(v: datetime | None) -> datetime | None:
    return v.replace(tzinfo=timezone.utc) if v and v.tzinfo is None else v  # SQLite drops tzinfo


class Progress(BaseModel):
    total: int
    completed: int
    failed: int
    invalid: int
    duplicate: int
    pending: int
    processing: int
    percent: float  # share of certificates that have reached a final state


class JobOut(BaseModel):
    job_id: str
    status: JobStatus
    event_name: str
    issuer: str
    issue_date: date
    created_at: datetime
    finished_at: datetime | None
    progress: Progress
    certificates_url: str
    archive_url: str | None

    _utc = field_validator("created_at", "finished_at")(_as_utc)


class CertificateOut(BaseModel):
    certificate_id: str
    index: int
    recipient_name: str
    recipient_email: str
    status: CertStatus
    error: str | None
    attempts: int
    download_url: str | None


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]


class VerifyOut(BaseModel):
    """Public. Deliberately has NO email field."""
    valid: bool
    certificate_id: str
    recipient_name: str
    event_name: str
    issuer: str
    issue_date: date
