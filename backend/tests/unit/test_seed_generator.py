from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from itertools import pairwise

import pytest

from app.seed.config import SeedConfig, SeedConfigError
from app.seed.generator import Dataset, Row, generate_dataset
from app.seed.vocab import INJECTION_NOTES

SMALL = SeedConfig(
    employees_per_company=20,
    days=21,
    overtime_breaches_per_company=3,
    buddy_pairs_per_company=1,
    buddy_days_per_pair=2,
    off_shift_accesses_per_company=2,
    injected_notes_per_company=2,
)
MAX_DAILY_HOURS = 12
BUDDY_WINDOW = timedelta(seconds=60)
SHIFT_GRACE = timedelta(hours=1)


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return generate_dataset(SeedConfig())


def _at(row: Row, column: str) -> datetime:
    value = row[column]
    assert isinstance(value, datetime)
    return value


def _by_id(rows: list[Row]) -> dict[object, Row]:
    return {row["id"]: row for row in rows}


def _worked_hours_by_day(data: Dataset) -> dict[tuple[object, date], float]:
    punches: dict[object, list[Row]] = defaultdict(list)
    for event in data.attendance_events:
        punches[event["employee_id"]].append(event)
    worked: dict[tuple[object, date], float] = {}
    for employee_id, events in punches.items():
        ordered = sorted(events, key=lambda event: _at(event, "occurred_at"))
        for clock_in, clock_out in zip(ordered[::2], ordered[1::2], strict=True):
            assert (clock_in["event_type"], clock_out["event_type"]) == ("clock_in", "clock_out")
            started = _at(clock_in, "occurred_at")
            ended = _at(clock_out, "occurred_at")
            worked[(employee_id, started.date())] = (ended - started).total_seconds() / 3600
    return worked


def _labels(data: Dataset, anomaly_type: str) -> list[Row]:
    return [label for label in data.anomaly_labels if label["anomaly_type"] == anomaly_type]


def test_same_seed_gives_identical_dataset() -> None:
    assert generate_dataset(SMALL) == generate_dataset(SMALL)


def test_different_seed_gives_different_rows() -> None:
    other = generate_dataset(replace(SMALL, seed=SMALL.seed + 1))
    assert other.attendance_events != generate_dataset(SMALL).attendance_events


def test_company_ids_do_not_depend_on_seed() -> None:
    other = generate_dataset(replace(SMALL, seed=SMALL.seed + 1))
    assert other.company_ids() == generate_dataset(SMALL).company_ids()


def test_default_scale_matches_plan(dataset: Dataset) -> None:
    assert len(dataset.companies) == 2
    per_company = Counter(employee["company_id"] for employee in dataset.employees)
    assert sorted(per_company.values()) == [200, 200]
    shift_days = {_at(shift, "starts_at").date() for shift in dataset.shifts}
    assert (max(shift_days) - min(shift_days)).days < 60
    assert len({run["period_start"] for run in dataset.payroll_runs}) == 5
    assert dataset.access_logs
    assert len(dataset.users) == 6


def test_injected_label_counts_per_company(dataset: Dataset) -> None:
    counts = Counter(
        (label["company_id"], label["anomaly_type"]) for label in dataset.anomaly_labels
    )
    for company_id in dataset.company_ids():
        assert counts[(company_id, "overtime_breach")] == 12
        assert counts[(company_id, "buddy_punching")] == 3 * 4
        assert counts[(company_id, "off_shift_access")] == 10


def test_injected_note_counts_per_company(dataset: Dataset) -> None:
    counts = Counter(
        event["company_id"]
        for event in dataset.attendance_events
        if event["note"] in INJECTION_NOTES
    )
    assert sorted(counts.values()) == [5, 5]


def test_only_labelled_days_break_the_daily_limit(dataset: Dataset) -> None:
    worked = _worked_hours_by_day(dataset)
    breaches = {key for key, hours in worked.items() if hours > MAX_DAILY_HOURS}
    labelled = {
        (label["employee_id"], label["occurred_on"])
        for label in _labels(dataset, "overtime_breach")
    }
    assert breaches == labelled


def test_overtime_label_points_at_the_late_clock_out(dataset: Dataset) -> None:
    events = _by_id(dataset.attendance_events)
    for label in _labels(dataset, "overtime_breach"):
        assert label["source_table"] == "attendance_events"
        assert events[label["source_id"]]["event_type"] == "clock_out"


def test_buddy_punch_shares_device_with_another_clock_in(dataset: Dataset) -> None:
    events = _by_id(dataset.attendance_events)
    for label in _labels(dataset, "buddy_punching"):
        punch = events[label["source_id"]]
        assert punch["event_type"] == "clock_in"
        assert punch["employee_id"] == label["employee_id"]
        partners = [
            other
            for other in dataset.attendance_events
            if other["event_type"] == "clock_in"
            and other["employee_id"] != punch["employee_id"]
            and other["device_id"] == punch["device_id"]
            and abs(_at(other, "occurred_at") - _at(punch, "occurred_at")) <= BUDDY_WINDOW
        ]
        assert partners


def test_buddy_punch_victim_never_badged_in_during_the_shift(dataset: Dataset) -> None:
    shifts: dict[tuple[object, object], Row] = {
        (shift["employee_id"], _at(shift, "starts_at").date()): shift for shift in dataset.shifts
    }
    for label in _labels(dataset, "buddy_punching"):
        shift = shifts[(label["employee_id"], label["occurred_on"])]
        window_start = _at(shift, "starts_at") - timedelta(hours=1)
        window_end = _at(shift, "ends_at") + timedelta(hours=1)
        badged = [
            log
            for log in dataset.access_logs
            if log["employee_id"] == label["employee_id"]
            and window_start <= _at(log, "occurred_at") <= window_end
        ]
        assert badged == []


def test_off_shift_access_is_outside_every_shift(dataset: Dataset) -> None:
    logs = _by_id(dataset.access_logs)
    for label in _labels(dataset, "off_shift_access"):
        entry = logs[label["source_id"]]
        assert entry["employee_id"] == label["employee_id"]
        overlapping = [
            shift
            for shift in dataset.shifts
            if shift["employee_id"] == entry["employee_id"]
            and _at(shift, "starts_at") <= _at(entry, "occurred_at") <= _at(shift, "ends_at")
        ]
        assert overlapping == []


def test_every_row_points_at_an_employee_of_its_own_company(dataset: Dataset) -> None:
    employer = {employee["id"]: employee["company_id"] for employee in dataset.employees}
    for _, rows in dataset.tables():
        for row in rows:
            if row.get("employee_id") is not None:
                assert employer[row["employee_id"]] == row["company_id"]


def test_only_labelled_clock_ins_share_a_device_within_the_buddy_window(dataset: Dataset) -> None:
    victims = {label["source_id"] for label in _labels(dataset, "buddy_punching")}
    by_device: dict[object, list[Row]] = defaultdict(list)
    for event in dataset.attendance_events:
        if event["event_type"] == "clock_in" and event["device_id"] is not None:
            by_device[event["device_id"]].append(event)
    suspicious: set[object] = set()
    for events in by_device.values():
        ordered = sorted(events, key=lambda event: _at(event, "occurred_at"))
        for first, second in pairwise(ordered):
            close = _at(second, "occurred_at") - _at(first, "occurred_at") <= BUDDY_WINDOW
            if close and first["employee_id"] != second["employee_id"]:
                suspicious.add(first["id"] if first["id"] in victims else second["id"])
    assert suspicious == victims


def test_only_labelled_access_falls_outside_the_shift(dataset: Dataset) -> None:
    labelled_entries = {label["source_id"] for label in _labels(dataset, "off_shift_access")}
    shifts: dict[object, list[Row]] = defaultdict(list)
    for shift in dataset.shifts:
        shifts[shift["employee_id"]].append(shift)
    outside = {
        log["id"]
        for log in dataset.access_logs
        if not any(
            _at(shift, "starts_at") - SHIFT_GRACE
            <= _at(log, "occurred_at")
            <= _at(shift, "ends_at") + SHIFT_GRACE
            for shift in shifts[log["employee_id"]]
        )
    }
    labelled_visits = {log["id"] for log in dataset.access_logs if log["door"] == "server-room"}
    assert labelled_entries <= outside
    assert outside == labelled_visits


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(SMALL, employees_per_company=0),
        lambda: replace(SMALL, days=6),
        lambda: replace(SMALL, overtime_breaches_per_company=-1),
        lambda: replace(SMALL, buddy_pairs_per_company=-1),
        lambda: replace(SMALL, buddy_days_per_pair=-1),
        lambda: replace(SMALL, off_shift_accesses_per_company=-1),
        lambda: replace(SMALL, injected_notes_per_company=-1),
    ],
)
def test_impossible_config_is_rejected(build: Callable[[], SeedConfig]) -> None:
    with pytest.raises(SeedConfigError):
        build()
