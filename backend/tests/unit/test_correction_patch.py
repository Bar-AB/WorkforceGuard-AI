import pytest

from app.errors import InvalidInputError
from app.services.corrections import (
    CorrectionTarget,
    PatchValue,
    check_reason,
    validate_patch,
)


def test_validate_patch_rejects_empty_patch() -> None:
    with pytest.raises(InvalidInputError, match="at least one"):
        validate_patch("shifts", {})


@pytest.mark.parametrize("key", ["id", "company_id", "employee_id"])
def test_validate_patch_rejects_locked_columns(key: str) -> None:
    with pytest.raises(InvalidInputError, match="shifts can be patched: starts_at, ends_at"):
        validate_patch("shifts", {key: "6f1c1a9e-8f43-4c38-9a59-6f3b1c2d4e5f"})


def test_validate_patch_unknown_column_error_lists_editable_columns_without_echoing_key() -> None:
    with pytest.raises(InvalidInputError) as caught:
        validate_patch("payroll_runs", {"gross_pay_bonus": 1})

    message = str(caught.value)
    assert "gross_pay_bonus" not in message
    assert "period_start, period_end, regular_hours, overtime_hours, gross_pay" in message


def test_validate_patch_rejects_wrong_type() -> None:
    with pytest.raises(InvalidInputError, match="regular_hours"):
        validate_patch("payroll_runs", {"regular_hours": "abc"})


def test_validate_patch_rejects_naive_datetime() -> None:
    with pytest.raises(InvalidInputError, match="occurred_at"):
        validate_patch("attendance_events", {"occurred_at": "2026-01-06T08:00:00"})


def test_validate_patch_rejects_null_for_non_nullable() -> None:
    with pytest.raises(InvalidInputError, match="door"):
        validate_patch("access_logs", {"door": None})


def test_validate_patch_allows_null_for_nullable() -> None:
    assert validate_patch("attendance_events", {"note": None}) == {"note": None}


def test_validate_patch_normalizes_decimal_and_datetime() -> None:
    patch = validate_patch("payroll_runs", {"regular_hours": 7.5}) | validate_patch(
        "shifts", {"ends_at": "2026-01-06T17:00:00+02:00"}
    )

    assert patch == {"regular_hours": "7.5", "ends_at": "2026-01-06T17:00:00+02:00"}


@pytest.mark.parametrize("reason", ["", "   ", "x" * 2001])
def test_check_reason_rejects_blank_and_too_long(reason: str) -> None:
    with pytest.raises(InvalidInputError, match="reason"):
        check_reason(reason)


def test_check_reason_strips_whitespace() -> None:
    assert check_reason("  clocked out twice  ") == "clocked out twice"


@pytest.mark.parametrize(
    ("table", "patch", "column"),
    [
        ("attendance_events", {"occurred_at": 8}, "occurred_at"),
        ("attendance_events", {"occurred_at": 1.5}, "occurred_at"),
        ("attendance_events", {"occurred_at": "1700000000"}, "occurred_at"),
        ("attendance_events", {"occurred_at": True}, "occurred_at"),
        ("shifts", {"starts_at": "2026-01-06"}, "starts_at"),
        ("payroll_runs", {"period_start": 0}, "period_start"),
        ("payroll_runs", {"period_start": "1700000000"}, "period_start"),
        ("payroll_runs", {"period_start": "2026-01-06T00:00:00+00:00"}, "period_start"),
        ("access_logs", {"granted": "yes"}, "granted"),
        ("access_logs", {"granted": 0.0}, "granted"),
        ("access_logs", {"granted": 1}, "granted"),
        ("payroll_runs", {"regular_hours": True}, "regular_hours"),
        ("payroll_runs", {"regular_hours": "8"}, "regular_hours"),
        ("access_logs", {"door": 5}, "door"),
    ],
)
def test_validate_patch_refuses_coerced_values(
    table: CorrectionTarget, patch: dict[str, PatchValue], column: str
) -> None:
    with pytest.raises(InvalidInputError, match=column):
        validate_patch(table, patch)


def test_validate_patch_error_names_expected_type_without_echoing_input() -> None:
    with pytest.raises(InvalidInputError) as caught:
        validate_patch("attendance_events", {"occurred_at": "1700000000"})

    message = str(caught.value)
    assert "ISO-8601" in message
    assert "time zone" in message
    assert "1700000000" not in message


def test_validate_patch_accepts_strict_values() -> None:
    assert validate_patch("payroll_runs", {"period_start": "2026-01-06", "gross_pay": 1200}) == {
        "period_start": "2026-01-06",
        "gross_pay": "1200",
    }
    assert validate_patch("access_logs", {"granted": False, "door": "B2"}) == {
        "granted": False,
        "door": "B2",
    }


@pytest.mark.parametrize(
    ("table", "patch", "column"),
    [
        ("attendance_events", {"event_type": "lunch"}, "event_type"),
        ("attendance_events", {"source": "x"}, "source"),
        ("access_logs", {"direction": "sideways"}, "direction"),
    ],
)
def test_validate_patch_refuses_values_outside_check_domain(
    table: CorrectionTarget, patch: dict[str, PatchValue], column: str
) -> None:
    with pytest.raises(InvalidInputError, match=column):
        validate_patch(table, patch)


def test_validate_patch_accepts_values_inside_check_domain() -> None:
    assert validate_patch("attendance_events", {"event_type": "clock_out", "source": "manual"}) == {
        "event_type": "clock_out",
        "source": "manual",
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"regular_hours": -5},
        {"overtime_hours": -0.25},
        {"gross_pay": -1},
        {"regular_hours": 123456789},
        {"regular_hours": 10000},
        {"regular_hours": 8.005},
        {"gross_pay": 10_000_000_000},
        {"gross_pay": 12.345},
    ],
)
def test_validate_patch_refuses_numbers_outside_column_shape(patch: dict[str, PatchValue]) -> None:
    with pytest.raises(InvalidInputError, match=next(iter(patch))):
        validate_patch("payroll_runs", patch)


def test_validate_patch_accepts_numbers_at_column_limits() -> None:
    assert validate_patch("payroll_runs", {"regular_hours": 9999.99, "gross_pay": 0}) == {
        "regular_hours": "9999.99",
        "gross_pay": "0",
    }


def test_validate_patch_refuses_text_over_length_cap() -> None:
    with pytest.raises(InvalidInputError, match="note"):
        validate_patch("attendance_events", {"note": "x" * 1001})


def test_validate_patch_accepts_text_at_length_cap() -> None:
    note = "x" * 1000

    assert validate_patch("attendance_events", {"note": note}) == {"note": note}


def test_check_reason_refuses_nul_character() -> None:
    with pytest.raises(InvalidInputError, match="reason must not contain NUL"):
        check_reason("clocked\x00 out twice")


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("attendance_events", "note"),
        ("attendance_events", "device_id"),
        ("access_logs", "door"),
    ],
)
def test_validate_patch_refuses_nul_in_text(table: CorrectionTarget, column: str) -> None:
    with pytest.raises(InvalidInputError, match=f"{column} must not contain NUL") as caught:
        validate_patch(table, {column: "B2\x00secret"})

    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("value", ["2026-01-06T08:00:00+14:01", "2026-01-06T08:00:00-20:00"])
def test_validate_patch_refuses_offsets_postgres_cannot_store(value: str) -> None:
    with pytest.raises(InvalidInputError, match="occurred_at needs a UTC offset within 14 hours"):
        validate_patch("attendance_events", {"occurred_at": value})


def test_validate_patch_accepts_offset_at_14_hours() -> None:
    patch = {"occurred_at": "2026-01-06T08:00:00+14:00"}

    assert validate_patch("attendance_events", patch) == patch


@pytest.mark.parametrize(
    ("table", "patch", "column"),
    [
        ("attendance_events", {"occurred_at": "0001-01-01T00:00:00+05:00"}, "occurred_at"),
        ("attendance_events", {"occurred_at": "1969-12-31T23:59:59+00:00"}, "occurred_at"),
        ("shifts", {"ends_at": "9999-12-31T23:00:00-05:00"}, "ends_at"),
        ("payroll_runs", {"period_start": "0001-01-01"}, "period_start"),
        ("payroll_runs", {"period_end": "2101-01-01"}, "period_end"),
    ],
)
def test_validate_patch_refuses_moments_outside_supported_years(
    table: CorrectionTarget, patch: dict[str, PatchValue], column: str
) -> None:
    with pytest.raises(InvalidInputError, match=f"{column} must fall between 1970 and 2100"):
        validate_patch(table, patch)


def test_validate_patch_accepts_supported_year_edges() -> None:
    assert validate_patch(
        "payroll_runs", {"period_start": "1970-01-01", "period_end": "2100-12-31"}
    ) == {"period_start": "1970-01-01", "period_end": "2100-12-31"}
