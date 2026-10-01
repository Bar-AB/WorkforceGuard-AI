import uuid
from datetime import date

import pytest

from evals.run_rules import Baselines, Score, render_report, score, shortfalls

COMPANY = uuid.UUID(int=1)


def _key(employee: int, day: int) -> tuple[uuid.UUID, uuid.UUID, date]:
    return (COMPANY, uuid.UUID(int=employee), date(2026, 1, day))


def test_score_counts_hits_misses_and_false_alarms() -> None:
    predicted = {_key(1, 1), _key(1, 2), _key(2, 3)}
    expected = {_key(1, 1), _key(1, 2), _key(3, 4)}

    result = score(predicted, expected)

    assert (result.true_positives, result.false_positives, result.false_negatives) == (2, 1, 1)
    assert result.precision == pytest.approx(2 / 3)
    assert result.recall == pytest.approx(2 / 3)
    assert result.f1 == pytest.approx(2 / 3)


def test_score_nothing_predicted_and_nothing_expected_is_perfect() -> None:
    result = score(set(), set())

    assert (result.precision, result.recall, result.f1) == (1.0, 1.0, 1.0)


def test_score_nothing_predicted_but_labels_exist_has_zero_recall_and_f1() -> None:
    result = score(set(), {_key(1, 1)})

    assert (result.precision, result.recall, result.f1) == (1.0, 0.0, 0.0)


def test_shortfalls_score_at_baseline_passes() -> None:
    baselines: Baselines = {"overtime_breach": {"f1": 1.0, "recall": 0.95}}

    assert shortfalls({"overtime_breach": Score(10, 0, 0)}, baselines) == []


def test_shortfalls_f1_below_baseline_is_reported() -> None:
    baselines: Baselines = {"overtime_breach": {"f1": 1.0, "recall": 0.5}}

    problems = shortfalls({"overtime_breach": Score(9, 1, 0)}, baselines)

    assert problems == ["overtime_breach: f1 0.947 is below baseline 1.000"]


def test_shortfalls_rule_without_baseline_is_reported() -> None:
    problems = shortfalls({"new_rule": Score(1, 0, 0)}, {})

    assert problems == ["new_rule: no baseline in evals/baselines.json"]


def test_shortfalls_baseline_without_scored_rule_is_reported() -> None:
    problems = shortfalls({}, {"overtime_breach": {"f1": 1.0}})

    assert problems == ["overtime_breach: has a baseline but was not scored"]


def test_render_report_lists_each_rule_with_its_scores() -> None:
    report = render_report({"overtime_breach": Score(9, 1, 0)}, [])

    assert "| overtime_breach | 0.900 | 1.000 | 0.947 | 9 | 1 | 0 |" in report
    assert "All rules meet their baselines." in report


def test_render_report_lists_shortfalls() -> None:
    report = render_report({}, ["overtime_breach: f1 0.5 is below baseline 1.0"])

    assert "- overtime_breach: f1 0.5 is below baseline 1.0" in report


def test_shortfalls_rule_with_no_labels_is_reported() -> None:
    problems = shortfalls({"overtime_breach": Score(0, 0, 0)}, {"overtime_breach": {"f1": 1.0}})

    assert problems == ["overtime_breach: no labelled anomalies (run make seed first)"]
