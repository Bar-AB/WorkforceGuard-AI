"""Overtime rule: a day over the daily limit, or a week over the weekly limit."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.errors import ConflictError
from app.rules.findings import RuleFinding

RULE_ID = "overtime_breach"
RULE_VERSION = "1"


class MissingOvertimePolicyError(ConflictError):
    """No overtime policy covers a worked day, so its hours cannot be judged."""


@dataclass(frozen=True)
class OvertimeLimits:
    max_daily_hours: Decimal
    max_weekly_hours: Decimal
    effective_from: date
    effective_to: date | None

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )


@dataclass(frozen=True)
class WorkedDay:
    employee_id: uuid.UUID
    # The date the shift started, so a night shift counts as one day.
    work_date: date
    hours: Decimal
    # Running total for the Sunday-to-Saturday week, this day included.
    week_hours_to_date: Decimal
    last_clock_out_id: uuid.UUID


def check_overtime(
    days: Sequence[WorkedDay], policies: Sequence[OvertimeLimits]
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for day in days:
        limits = _limits_on(day.work_date, policies)
        if day.hours > limits.max_daily_hours:
            findings.append(_daily_finding(day, limits))
        if _crosses_weekly_limit(day, limits):
            findings.append(_weekly_finding(day, limits))
    return findings


def _limits_on(day: date, policies: Sequence[OvertimeLimits]) -> OvertimeLimits:
    covering = [policy for policy in policies if policy.covers(day)]
    if not covering:
        raise MissingOvertimePolicyError(f"No overtime policy is in effect on {day}.")
    return max(covering, key=lambda policy: policy.effective_from)


def _crosses_weekly_limit(day: WorkedDay, limits: OvertimeLimits) -> bool:
    before = day.week_hours_to_date - day.hours
    return before <= limits.max_weekly_hours < day.week_hours_to_date


def _daily_finding(day: WorkedDay, limits: OvertimeLimits) -> RuleFinding:
    max_hours = _plain(limits.max_daily_hours)
    return _finding(
        day,
        variant="daily",
        severity="high",
        summary=f"Worked {day.hours} h, daily limit {max_hours} h.",
        evidence={"limit": "daily", "hours": str(day.hours), "max_hours": max_hours},
    )


def _weekly_finding(day: WorkedDay, limits: OvertimeLimits) -> RuleFinding:
    hours, max_hours = str(day.week_hours_to_date), _plain(limits.max_weekly_hours)
    return _finding(
        day,
        variant="weekly",
        severity="medium",
        summary=f"Worked {hours} h this week, weekly limit {max_hours} h.",
        evidence={"limit": "weekly", "hours": hours, "max_hours": max_hours},
    )


def _finding(
    day: WorkedDay, *, variant: str, severity: str, summary: str, evidence: dict[str, str]
) -> RuleFinding:
    return RuleFinding(
        rule_id=RULE_ID,
        rule_version=RULE_VERSION,
        employee_id=day.employee_id,
        occurred_on=day.work_date,
        variant=variant,
        severity=severity,
        summary=summary,
        evidence={
            **evidence,
            "source_table": "attendance_events",
            "source_id": str(day.last_clock_out_id),
        },
    )


def _plain(hours: Decimal) -> str:
    """12.00 -> "12", 10.50 -> "10.5", never scientific notation."""
    return f"{hours.normalize():f}"
