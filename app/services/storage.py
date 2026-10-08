"""Where files live on disk. The DB stores paths RELATIVE to STORAGE_DIR."""
import os
import re
import unicodedata
import uuid
from pathlib import Path

from app.config import get_settings


def root() -> Path:
    p = Path(get_settings().storage_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def job_dir(job_id: str) -> Path:
    d = root() / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve(relative: str) -> Path:
    """Relative DB path -> absolute path, refusing anything that escapes STORAGE_DIR."""
    full = (root() / relative).resolve()
    if root() not in full.parents:
        raise ValueError("path escapes storage directory")
    return full


def write_atomic(path: Path, data: bytes) -> None:
    """Write to a temp file then rename. A reader (or a crash) never sees a half-written file."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on Windows and POSIX
    finally:
        tmp.unlink(missing_ok=True)


def write_certificate(job_id: str, certificate_id: str, pdf: bytes) -> str:
    path = job_dir(job_id) / f"{certificate_id}.pdf"
    write_atomic(path, pdf)
    return path.relative_to(root()).as_posix()


def safe_filename(name: str, fallback: str = "certificate") -> str:
    """Turn any recipient name into text that is safe as a file name on every OS."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return text[:60] or fallback
