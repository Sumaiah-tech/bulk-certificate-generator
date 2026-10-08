"""Per-recipient validation + duplicate detection. Pure functions: no DB, no I/O."""
import unicodedata
from dataclasses import dataclass
from typing import Any

from email_validator import EmailNotValidError, validate_email

from app.models import CertStatus

MAX_NAME_LENGTH = 80


def latin_problem(text: str) -> str | None:
    """ReportLab's built-in fonts only draw Western (cp1252) characters."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return "contains characters the certificate font cannot draw (Latin letters only)"
    return None


def has_control_chars(text: str) -> bool:
    return any(unicodedata.category(ch).startswith("C") for ch in text)


@dataclass
class Checked:
    index: int
    name: str
    email: str
    status: CertStatus  # PENDING (= valid, still to generate), INVALID or DUPLICATE
    error: str | None = None


def _check_name(raw: Any) -> tuple[str, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return "", "name is required"
    name = " ".join(raw.split())  # trim + collapse inner whitespace
    if has_control_chars(name):
        return name, "name contains control characters"
    if len(name) > MAX_NAME_LENGTH:
        return name, f"name is longer than {MAX_NAME_LENGTH} characters"
    if problem := latin_problem(name):
        return name, f"name {problem}"
    return name, None


def _check_email(raw: Any) -> tuple[str, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return "", "email is required"
    try:
        # check_deliverability=False: syntax check only, no DNS lookups.
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower(), None
    except EmailNotValidError as exc:
        return raw.strip()[:200], f"email is invalid ({exc})"


def validate_recipients(raw_rows: list[tuple[Any, Any]]) -> list[Checked]:
    """Validate every (name, email) pair. A bad row never raises: it is marked INVALID/DUPLICATE."""
    results: list[Checked] = []
    first_seen: dict[str, int] = {}  # normalised email -> index of first VALID row using it
    for i, (raw_name, raw_email) in enumerate(raw_rows):
        name, name_err = _check_name(raw_name)
        email, email_err = _check_email(raw_email)
        errors = [e for e in (name_err, email_err) if e]
        if errors:
            results.append(Checked(i, name, email, CertStatus.INVALID, "; ".join(errors)))
        elif email in first_seen:
            results.append(Checked(i, name, email, CertStatus.DUPLICATE,
                                   f"duplicate email (first seen at index {first_seen[email]})"))
        else:
            first_seen[email] = i
            results.append(Checked(i, name, email, CertStatus.PENDING))
    return results
