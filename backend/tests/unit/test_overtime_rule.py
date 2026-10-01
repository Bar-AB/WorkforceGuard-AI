import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.rules.findings import RuleFinding
from app.rules.overtime import (
    RULE_ID,
    MissingOvertimePolicyError,
    OvertimeLimits,
    WorkedDay,
    check_overtime,
)

EMPLOYEE = uuid.UUID(int=1)
CLOCK_OUT = uuid.UUID(int=2)
ISRAEL = OvertimeLimits(
    max_daily_hours=Decimal(12),
    max_weekly_hours=Decimal(58),
    effective_from=date(2025, 1, 1),
    effective_to=None,
)


def _day(hours: str, week_to_date: str | None = None, on: date = date(2026, 1, 6)) -> WorkedDay:
    return WorkedDay(
        employee_id=EMPLOYEE,
        work_date=on,
        hours=Decimal(hours),
        week_hours_to_date=Decimal(week_to_date or hours),
        last_clock_out_id=CLOCK_OUT,
    )


def test_check_overtime_day_over_daily_limit_returns_daily_finding() -> None:
    findings = check_overtime([_day("12.50")], [ISRAEL])

    assert findings == [
        RuleFinding(
            rule_id=RULE_ID,
            rule_version="1",
            employee_id=EMPLOYEE,
            occurred_on=date(2026, 1, 6),
            severity="high",
            summary="Worked 12.50 h, daily limit 12 h.",
            evidence={
                "limit": "daily",
                "hours": "12.50",
                "max_hours": "12",
                "source_table": "attendance_events",
                "source_id": str(CLOCK_OUT),
            },
        )
    ]


@pytest.mark.parametrize("hours", ["8.40", "11.30", "12.00"])
def test_check_overtime_day_within_daily_limit_returns_nothing(hours: str) -> None:
    assert check_overtime([_day(hours)], [ISRAEL]) == []


def test_check_overtime_day_crossing_weekly_limit_returns_weekly_finding() -> None:
    findings = check_overtime([_day("9.00", week_to_date="59.00")], [ISRAEL])

    assert [(f.occurred_on, f.severity, f.evidence["limit"]) for f in findings] == [
        (date(2026, 1, 6), "medium", "weekly")
    ]
    assert findings[0].summary == "Worked 59.00 h this week, weekly limit 58 h."


def test_check_overtime_days_after_weekly_crossing_are_not_reported_again() -> None:
    days = [
        _day("9.00", week_to_date="59.00", on=date(2026, 1, 7)),
        _day("8.00", week_to_date="67.00", on=date(2026, 1, 8)),
    ]

    findings = check_overtime(days, [ISRAEL])

    assert [f.occurred_on for f in findings] == [date(2026, 1, 7)]


def test_check_overtime_week_exactly_at_limit_returns_nothing() -> None:
    assert check_overtime([_day("8.00", week_to_date="58.00")], [ISRAEL]) == []


def test_check_overtime_long_day_that_crosses_week_limit_returns_both_findings() -> None:
    findings = check_overtime([_day("13.00", week_to_date="60.00")], [ISRAEL])

    assert [f.evidence["limit"] for f in findings] == ["daily", "weekly"]


def test_check_overtime_uses_policy_in_effect_on_the_work_date() -> None:
    old = OvertimeLimits(Decimal(10), Decimal(50), date(2025, 1, 1), date(2025, 12, 31))
    new = OvertimeLimits(Decimal(12), Decimal(58), date(2026, 1, 1), None)

    findings = check_overtime([_day("11.00", on=date(2025, 12, 31)), _day("11.00")], [old, new])

    assert [f.occurred_on for f in findings] == [date(2025, 12, 31)]


def test_check_overtime_without_policy_for_the_date_raises() -> None:
    later = OvertimeLimits(Decimal(12), Decimal(58), date(2027, 1, 1), None)

    with pytest.raises(MissingOvertimePolicyError, match="2026-01-06"):
        check_overtime([_day("8.00")], [later])
