"""Two tables: Job (one request) and Certificate (one row per recipient in that request)."""
import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class CertStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"  # valid recipient, generation failed
    INVALID = "INVALID"  # recipient data rejected
    DUPLICATE = "DUPLICATE"  # same email earlier in the same job


# Statuses after which a certificate will never change again.
TERMINAL = {CertStatus.COMPLETED, CertStatus.FAILED, CertStatus.INVALID, CertStatus.DUPLICATE}


def _enum(e):  # stored as plain strings: portable, and no ALTER TYPE pain on PostgreSQL
    return Enum(e, native_enum=False, length=32, validate_strings=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(200), default="")
    issue_date: Mapped[date] = mapped_column(Date)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.PENDING)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_path: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "index"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)  # 0-based position in the request
    recipient_name: Mapped[str] = mapped_column(String(500), default="")
    recipient_email: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[CertStatus] = mapped_column(_enum(CertStatus), default=CertStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to STORAGE_DIR
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
