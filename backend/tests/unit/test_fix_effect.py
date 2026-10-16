import uuid
from datetime import date
from typing import Final

import pytest

from app.rules.findings import RuleFinding
from app.services.correction_guard import FixEffect, fix_effect

EMPLOYEE: Final = uuid.uuid4()
COLLEAGUE: Final = uuid.uuid4()
TUESDAY: Final = date(2026, 1, 6)
MONDAY: Final = date(2026, 1, 5)


def _daily(employee_id: uuid.UUID, day: date, hours: str | None) -> RuleFinding:
    evidence = {"limit": "daily"} if hours is None else {"limit": "daily", "hours": hours}
    return RuleFinding(
        rule_id="overtime_breach",
        rule_version="1",
        employee_id=employee_id,
        occurred_on=day,
        variant="daily",
        severity="high",
        summary="Too many hours.",
        evidence=evidence,
    )


def _detected(*findings: RuleFinding) -> dict[str, RuleFinding]:
    return {finding.dedup_key: finding for finding in findings}


TARGET: Final = _daily(EMPLOYEE, TUESDAY, "13.00")


def _effect(before: dict[str, RuleFinding], after: dict[str, RuleFinding]) -> FixEffect:
    return fix_effect(before, after, TARGET.dedup_key, EMPLOYEE)


def test_fix_effect_resolves_when_only_the_target_disappears() -> None:
    effect = _effect(_detected(TARGET), _detected())

    assert (effect, effect.verdict) == (FixEffect(False, 0), "resolves")


def test_fix_effect_still_detected_when_the_target_survives() -> None:
    effect = _effect(_detected(TARGET), _detected(_daily(EMPLOYEE, TUESDAY, "12.99")))

    assert (effect, effect.verdict) == (FixEffect(True, 0), "still_detected")


def test_fix_effect_ignores_a_new_breach_of_another_employee() -> None:
    effect = _effect(_detected(TARGET), _detected(_daily(COLLEAGUE, TUESDAY, "14.00")))

    assert effect.verdict == "resolves"


def test_fix_effect_ignores_a_bigger_breach_of_another_employee() -> None:
    before = _detected(TARGET, _daily(COLLEAGUE, MONDAY, "13.00"))
    after = _detected(_daily(COLLEAGUE, MONDAY, "20.00"))

    assert _effect(before, after).verdict == "resolves"


def test_fix_effect_moves_problem_for_a_new_breach_of_the_same_employee() -> None:
    effect = _effect(_detected(TARGET), _detected(_daily(EMPLOYEE, MONDAY, "21.02")))

    assert (effect, effect.verdict) == (FixEffect(False, 1), "moves_problem")


def test_fix_effect_moves_problem_when_an_existing_breach_of_the_same_employee_grows() -> None:
    before = _detected(TARGET, _daily(EMPLOYEE, MONDAY, "13.00"))
    after = _detected(_daily(EMPLOYEE, MONDAY, "34.02"))

    assert _effect(before, after).verdict == "moves_problem"


def test_fix_effect_resolves_when_an_existing_breach_of_the_same_employee_does_not_grow() -> None:
    before = _detected(TARGET, _daily(EMPLOYEE, MONDAY, "13.00"))
    after = _detected(_daily(EMPLOYEE, MONDAY, "13.00"))

    assert _effect(before, after).verdict == "resolves"


@pytest.mark.parametrize(("old", "new"), [(None, "13.00"), ("13.00", None), ("13.00", "n/a")])
def test_fix_effect_counts_a_breach_without_readable_hours_as_worse(
    old: str | None, new: str | None
) -> None:
    before = _detected(TARGET, _daily(EMPLOYEE, MONDAY, old))
    after = _detected(_daily(EMPLOYEE, MONDAY, new))

    assert _effect(before, after).verdict == "moves_problem"
