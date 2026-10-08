"""Unit tests for per-recipient validation (no HTTP, no DB)."""
from app.models import CertStatus
from app.services.validation import validate_recipients


def check(*pairs):
    return validate_recipients(list(pairs))


def test_valid_recipient_is_pending_and_normalised():
    [r] = check(("  Ada   Lovelace ", "Ada@Example.COM"))
    assert r.status == CertStatus.PENDING and r.error is None
    assert r.name == "Ada Lovelace" and r.email == "ada@example.com"


def test_missing_name_is_invalid():
    [r] = check((None, "a@example.com"))
    assert r.status == CertStatus.INVALID and "name is required" in r.error


def test_blank_name_is_invalid():
    [r] = check(("   ", "a@example.com"))
    assert r.status == CertStatus.INVALID


def test_bad_email_is_invalid_with_reason():
    [r] = check(("Ada", "oops"))
    assert r.status == CertStatus.INVALID and r.error.startswith("email is invalid")


def test_missing_email_is_invalid():
    [r] = check(("Ada", None))
    assert r.status == CertStatus.INVALID and "email is required" in r.error


def test_both_errors_are_reported_together():
    [r] = check(("", "nope"))
    assert "name is required" in r.error and "email is invalid" in r.error


def test_non_string_values_do_not_crash():
    [r] = check((123, ["x"]))
    assert r.status == CertStatus.INVALID


def test_non_latin_name_is_rejected_cleanly():
    [r] = check(("ಅಡಾ", "a@example.com"))  # Kannada
    assert r.status == CertStatus.INVALID and "Latin" in r.error


def test_accented_latin_name_is_fine():
    [r] = check(("José Zoë Müller", "j@example.com"))
    assert r.status == CertStatus.PENDING


def test_control_characters_rejected():
    [r] = check(("Ada\x00Lovelace", "a@example.com"))
    assert r.status == CertStatus.INVALID


def test_overlong_name_rejected():
    [r] = check(("x" * 81, "a@example.com"))
    assert r.status == CertStatus.INVALID


def test_duplicate_email_flagged_case_insensitively():
    rows = check(("Ada", "ada@example.com"), ("Ada Again", "ADA@example.com"))
    assert rows[0].status == CertStatus.PENDING
    assert rows[1].status == CertStatus.DUPLICATE and "first seen at index 0" in rows[1].error


def test_invalid_first_row_does_not_make_later_row_a_duplicate():
    rows = check(("", "ada@example.com"), ("Ada", "ada@example.com"))
    assert rows[0].status == CertStatus.INVALID and rows[1].status == CertStatus.PENDING


def test_indexes_follow_input_order():
    assert [r.index for r in check(("a", "a@example.com"), ("", ""), ("c", "c@example.com"))] == [0, 1, 2]
