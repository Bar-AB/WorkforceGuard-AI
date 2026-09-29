"""Seeded synthetic HR data: the same config always yields the same rows, ids included."""

import random
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Table

from app.db.models import Base
from app.seed.config import SeedConfig
from app.seed.vocab import (
    COMPANY_NAMES,
    DEPARTMENTS,
    FIRST_NAMES,
    INJECTION_NOTES,
    LAST_NAMES,
    MAIN_DOOR,
    MANUAL_NOTES,
    RESTRICTED_DOOR,
    TERMINALS,
)

Row = dict[str, object]
# (index into the company's staff list, shift start date)
Slot = tuple[int, date]

COMPANY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "seed.workforceguard.example")

SUNDAY_TO_THURSDAY = frozenset({6, 0, 1, 2, 3})
SATURDAY = 5
SHIFT_STARTS = (time(7), time(15), time(23))
SHIFT_WEIGHTS = (60, 25, 15)
SHIFT_LENGTH = timedelta(hours=8)

# Israeli Hours of Work and Rest Law: 8.6 h standard day on a 5-day week, 12 h daily cap,
# 42 h standard week plus 16 h weekly overtime.
STANDARD_DAILY_HOURS = Decimal("8.6")
MAX_DAILY_HOURS = Decimal(12)
MAX_WEEKLY_HOURS = Decimal(58)
FIRST_OVERTIME_HOURS = Decimal(2)
FIRST_OVERTIME_RATE = Decimal("1.25")
LATER_OVERTIME_RATE = Decimal("1.5")
PAY_PERIOD_DAYS = 14

MANUAL_SHARE = 0.02
MOBILE_SHARE = 0.08
# Legal overtime (9-11.3 h days) so rules are also tested against near misses.
NEAR_MISS_SHARE = 0.03
HOUR = 3600
# Gap between two colleagues clocking in on one terminal. It stays wider than the
# buddy offset plus any buddy-punch window up to 60 s, so only labelled pairs look alike.
ARRIVAL_GAP_SECONDS = (120, 200)
BUDDY_OFFSET_SECONDS = (3, 40)
FIRST_ARRIVAL_LEAD = timedelta(minutes=30)
SCHEDULE_STEP = timedelta(minutes=15)


class _SlotKind(StrEnum):
    NORMAL = "normal"
    BREACH = "breach"
    BUDDY = "buddy"
    VICTIM = "victim"
    INJECTED = "injected"


@dataclass(frozen=True)
class _Staff:
    id: uuid.UUID
    number: str
    shift_start: time
    terminal: str
    hourly_rate: Decimal


@dataclass(frozen=True)
class _ClockSource:
    source: str
    device_id: str | None
    note: str | None


@dataclass(frozen=True)
class _Evidence:
    table: str
    row_id: uuid.UUID
    notes: str


@dataclass(frozen=True)
class _Punch:
    clock_in: datetime
    clock_out: datetime
    # Overtime is planned, so the shift is scheduled to cover it and the badge-out
    # stays inside the shift window. None means the standard 8 h shift.
    planned_end: datetime | None = None


@dataclass
class Dataset:
    companies: list[Row] = field(default_factory=list)
    overtime_policies: list[Row] = field(default_factory=list)
    employees: list[Row] = field(default_factory=list)
    users: list[Row] = field(default_factory=list)
    shifts: list[Row] = field(default_factory=list)
    attendance_events: list[Row] = field(default_factory=list)
    access_logs: list[Row] = field(default_factory=list)
    payroll_runs: list[Row] = field(default_factory=list)
    anomaly_labels: list[Row] = field(default_factory=list)

    def tables(self) -> list[tuple[Table, list[Row]]]:
        """Tables in foreign-key order, parents first."""
        rows_by_table = {
            "companies": self.companies,
            "overtime_policies": self.overtime_policies,
            "employees": self.employees,
            "users": self.users,
            "shifts": self.shifts,
            "attendance_events": self.attendance_events,
            "access_logs": self.access_logs,
            "payroll_runs": self.payroll_runs,
            "anomaly_labels": self.anomaly_labels,
        }
        return [(Base.metadata.tables[name], rows) for name, rows in rows_by_table.items()]

    def company_ids(self) -> list[uuid.UUID]:
        return [company_id_for(name) for name in COMPANY_NAMES]


def company_id_for(name: str) -> uuid.UUID:
    """Stable across seeds, so re-seeding with another seed replaces the same companies."""
    return uuid.uuid5(COMPANY_NAMESPACE, name)


def generate_dataset(config: SeedConfig) -> Dataset:
    rng = random.Random(config.seed)
    dataset = Dataset()
    for name in COMPANY_NAMES:
        _CompanyGenerator(rng, config, dataset, name).run()
    return dataset


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=UTC)


def _round_up(moment: datetime, step: timedelta) -> datetime:
    remainder = (moment - datetime.min.replace(tzinfo=UTC)) % step
    return moment + (step - remainder) % step


def _hours(punch: _Punch) -> Decimal:
    seconds = int((punch.clock_out - punch.clock_in).total_seconds())
    return (Decimal(seconds) / HOUR).quantize(Decimal("0.01"))


def _pay(daily_hours: list[Decimal], rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    regular = overtime = gross = Decimal(0)
    for hours in daily_hours:
        extra = max(hours - STANDARD_DAILY_HOURS, Decimal(0))
        first = min(extra, FIRST_OVERTIME_HOURS)
        regular += hours - extra
        overtime += extra
        gross += (hours - extra) * rate
        gross += first * rate * FIRST_OVERTIME_RATE + (extra - first) * rate * LATER_OVERTIME_RATE
    cent = Decimal("0.01")
    return regular.quantize(cent), overtime.quantize(cent), gross.quantize(cent)


class _CompanyGenerator:
    def __init__(self, rng: random.Random, config: SeedConfig, data: Dataset, name: str) -> None:
        self.rng = rng
        self.config = config
        self.data = data
        self.name = name
        self.company_id = company_id_for(name)
        self.slug = name.lower().replace(" ", "-")
        all_days = [config.start_date + timedelta(days=offset) for offset in range(config.days)]
        self.work_days = [day for day in all_days if day.weekday() in SUNDAY_TO_THURSDAY]
        self.saturdays = [day for day in all_days if day.weekday() == SATURDAY]
        self.staff: list[_Staff] = []

    def run(self) -> None:
        self._add_company()
        self._add_policy()
        self._add_employees()
        self._add_users()
        slots = [(index, day) for index in range(len(self.staff)) for day in self.work_days]
        punches = self._normal_punches(slots)
        kinds = self._plan_slot_kinds(slots, punches)
        for slot in slots:
            self._add_slot(slot, punches[slot], kinds.get(slot, _SlotKind.NORMAL))
        self._add_off_shift_accesses()
        self._add_payroll(slots, punches)

    def _new_id(self) -> uuid.UUID:
        return uuid.UUID(int=self.rng.getrandbits(128), version=4)

    def _seconds(self, low: int, high: int) -> timedelta:
        return timedelta(seconds=self.rng.randint(low, high))

    def _add_company(self) -> None:
        created = _at(self.config.start_date - timedelta(days=365), time(0))
        self.data.companies.append(
            {"id": self.company_id, "name": self.name, "created_at": created}
        )

    def _add_policy(self) -> None:
        self.data.overtime_policies.append(
            {
                "id": self._new_id(),
                "company_id": self.company_id,
                "name": "Israel standard (Hours of Work and Rest Law)",
                "max_daily_hours": MAX_DAILY_HOURS,
                "max_weekly_hours": MAX_WEEKLY_HOURS,
                "effective_from": self.config.start_date - timedelta(days=365),
                "effective_to": None,
            }
        )

    def _add_employees(self) -> None:
        for number in range(1, self.config.employees_per_company + 1):
            department_index = (number - 1) % len(DEPARTMENTS)
            is_department_head = number <= len(DEPARTMENTS)
            manager = None if is_department_head else self.staff[department_index].id
            staff = _Staff(
                id=self._new_id(),
                number=f"E{number:05d}",
                shift_start=self.rng.choices(SHIFT_STARTS, weights=SHIFT_WEIGHTS)[0],
                terminal=f"{self.slug}/{self.rng.choice(TERMINALS)}",
                hourly_rate=Decimal(self.rng.randint(4000, 12000)) / 100,
            )
            first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
            self.staff.append(staff)
            self.data.employees.append(
                {
                    "id": staff.id,
                    "company_id": self.company_id,
                    "employee_number": staff.number,
                    "full_name": f"{first} {last}",
                    "email": f"{first}.{last}.{number}@{self.slug}.example".lower(),
                    "department": DEPARTMENTS[department_index],
                    "manager_id": manager,
                    "hired_on": self.config.start_date - timedelta(days=self.rng.randint(30, 3650)),
                    "terminated_on": None,
                }
            )

    def _add_users(self) -> None:
        created = _at(self.config.start_date - timedelta(days=30), time(0))
        for role, employee in (("admin", None), ("manager", self.staff[0].id), ("auditor", None)):
            self.data.users.append(
                {
                    "id": self._new_id(),
                    "company_id": self.company_id,
                    "email": f"{role}@{self.slug}.example",
                    "display_name": f"{self.name} {role.title()}",
                    "role": role,
                    "employee_id": employee,
                    "created_at": created,
                }
            )

    def _shift_start(self, slot: Slot) -> datetime:
        index, day = slot
        return _at(day, self.staff[index].shift_start)

    def _normal_punches(self, slots: list[Slot]) -> dict[Slot, _Punch]:
        """Colleagues on one terminal and shift arrive one by one, never seconds apart."""
        queues: dict[tuple[date, time, str], list[Slot]] = defaultdict(list)
        for slot in slots:
            staff = self.staff[slot[0]]
            queues[(slot[1], staff.shift_start, staff.terminal)].append(slot)
        punches: dict[Slot, _Punch] = {}
        for queue in queues.values():
            self.rng.shuffle(queue)
            arrival = self._shift_start(queue[0]) - FIRST_ARRIVAL_LEAD
            for slot in queue:
                arrival += self._seconds(*ARRIVAL_GAP_SECONDS)
                leaving = self._shift_start(slot) + SHIFT_LENGTH + self._seconds(-300, 600)
                punches[slot] = _Punch(arrival, leaving)
        return punches

    def _overtime(self, punch: _Punch, low_hours: float, high_hours: float) -> _Punch:
        """Same arrival, but a planned long day of low to high hours in total."""
        worked = self._seconds(int(low_hours * HOUR), int(high_hours * HOUR))
        clock_out = punch.clock_in + worked
        return _Punch(punch.clock_in, clock_out, _round_up(clock_out, SCHEDULE_STEP))

    def _plan_slot_kinds(
        self, slots: list[Slot], punches: dict[Slot, _Punch]
    ) -> dict[Slot, _SlotKind]:
        """Marks anomaly slots and rewrites their punches. Unmarked slots stay normal."""
        kinds: dict[Slot, _SlotKind] = {}
        for victim, buddy in self._pick_buddy_slots().items():
            kinds[victim] = _SlotKind.VICTIM
            kinds[buddy] = _SlotKind.BUDDY
            partner = punches[buddy]
            punches[victim] = _Punch(
                partner.clock_in + self._seconds(*BUDDY_OFFSET_SECONDS),
                partner.clock_out + self._seconds(*BUDDY_OFFSET_SECONDS),
            )
        free = [slot for slot in slots if slot not in kinds]
        breaches = self.config.overtime_breaches_per_company
        chosen = self.rng.sample(free, breaches + self.config.injected_notes_per_company)
        for slot in chosen[:breaches]:
            kinds[slot] = _SlotKind.BREACH
            punches[slot] = self._overtime(punches[slot], 12.5, 14)
        for slot in chosen[breaches:]:
            kinds[slot] = _SlotKind.INJECTED
        for slot in free:
            if slot not in kinds and self.rng.random() < NEAR_MISS_SHARE:
                punches[slot] = self._overtime(punches[slot], 9, 11.3)
        return kinds

    def _pick_buddy_slots(self) -> dict[Slot, Slot]:
        """Victim slot -> buddy slot. A buddy shares the victim's shift and terminal."""
        order = list(range(len(self.staff)))
        self.rng.shuffle(order)
        used: set[int] = set()
        pairs: list[tuple[int, int]] = []
        for victim in order:
            if len(pairs) == self.config.buddy_pairs_per_company:
                break
            if victim in used:
                continue
            buddy = next((other for other in order if self._can_cover(other, victim, used)), None)
            if buddy is not None:
                used.update((victim, buddy))
                pairs.append((victim, buddy))
        if len(pairs) < self.config.buddy_pairs_per_company:
            raise ValueError("Not enough employees sharing a shift and terminal for buddy pairs.")
        slots: dict[Slot, Slot] = {}
        for victim, buddy in pairs:
            for day in sorted(self.rng.sample(self.work_days, self.config.buddy_days_per_pair)):
                slots[(victim, day)] = (buddy, day)
        return slots

    def _can_cover(self, other: int, victim: int, used: set[int]) -> bool:
        a, b = self.staff[other], self.staff[victim]
        same_post = (a.shift_start, a.terminal) == (b.shift_start, b.terminal)
        return other != victim and other not in used and same_post

    def _clock_source(self, staff: _Staff, kind: _SlotKind) -> _ClockSource:
        if kind is _SlotKind.INJECTED:
            return _ClockSource("manual", None, self.rng.choice(INJECTION_NOTES))
        if kind is not _SlotKind.NORMAL:
            return _ClockSource("terminal", staff.terminal, None)
        roll = self.rng.random()
        if roll < MANUAL_SHARE:
            return _ClockSource("manual", None, self.rng.choice(MANUAL_NOTES))
        if roll < MANUAL_SHARE + MOBILE_SHARE:
            return _ClockSource("mobile", f"{self.slug}/MOBILE-{staff.number}", None)
        return _ClockSource("terminal", staff.terminal, None)

    def _add_slot(self, slot: Slot, punch: _Punch, kind: _SlotKind) -> None:
        staff, day = self.staff[slot[0]], slot[1]
        starts = self._shift_start(slot)
        self.data.shifts.append(
            {
                "id": self._new_id(),
                "company_id": self.company_id,
                "employee_id": staff.id,
                "starts_at": starts,
                "ends_at": punch.planned_end or starts + SHIFT_LENGTH,
            }
        )
        clock = self._clock_source(staff, kind)
        clock_in = self._add_punch(staff, "clock_in", punch.clock_in, clock)
        # The note explains the manual clock-in; repeating it on the clock-out adds nothing.
        clock_out = self._add_punch(staff, "clock_out", punch.clock_out, replace(clock, note=None))
        if kind is not _SlotKind.VICTIM:
            self._add_access(staff, MAIN_DOOR, "in", punch.clock_in - self._seconds(60, 300))
            self._add_access(staff, MAIN_DOOR, "out", punch.clock_out + self._seconds(60, 300))
        if kind is _SlotKind.BREACH:
            notes = f"Worked {_hours(punch)} h, daily limit {MAX_DAILY_HOURS} h."
            evidence = _Evidence("attendance_events", clock_out, notes)
            self._add_label(staff, "overtime_breach", day, evidence)
        if kind is _SlotKind.VICTIM:
            notes = (
                "Clocked in right after a colleague on one terminal; no badge-in during the shift."
            )
            evidence = _Evidence("attendance_events", clock_in, notes)
            self._add_label(staff, "buddy_punching", day, evidence)

    def _add_punch(
        self, staff: _Staff, event_type: str, occurred_at: datetime, clock: _ClockSource
    ) -> uuid.UUID:
        event_id = self._new_id()
        self.data.attendance_events.append(
            {
                "id": event_id,
                "company_id": self.company_id,
                "employee_id": staff.id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "source": clock.source,
                "device_id": clock.device_id,
                "note": clock.note,
            }
        )
        return event_id

    def _add_access(
        self, staff: _Staff, door: str, direction: str, occurred_at: datetime
    ) -> uuid.UUID:
        log_id = self._new_id()
        self.data.access_logs.append(
            {
                "id": log_id,
                "company_id": self.company_id,
                "employee_id": staff.id,
                "door": door,
                "direction": direction,
                "granted": True,
                "occurred_at": occurred_at,
            }
        )
        return log_id

    def _add_off_shift_accesses(self) -> None:
        # Saturdays have no shifts and no overnight spill-over, so any visit is off-shift.
        candidates = [(index, day) for index in range(len(self.staff)) for day in self.saturdays]
        for index, day in self.rng.sample(candidates, self.config.off_shift_accesses_per_company):
            staff = self.staff[index]
            entered = _at(day, time(9)) + self._seconds(0, 9 * HOUR)
            entry = self._add_access(staff, RESTRICTED_DOOR, "in", entered)
            self._add_access(staff, RESTRICTED_DOOR, "out", entered + self._seconds(1200, 5400))
            notes = f"Entered {RESTRICTED_DOOR} on a day with no shift."
            self._add_label(staff, "off_shift_access", day, _Evidence("access_logs", entry, notes))

    def _add_label(self, staff: _Staff, anomaly_type: str, day: date, evidence: _Evidence) -> None:
        self.data.anomaly_labels.append(
            {
                "id": self._new_id(),
                "company_id": self.company_id,
                "employee_id": staff.id,
                "anomaly_type": anomaly_type,
                "occurred_on": day,
                "source_table": evidence.table,
                "source_id": evidence.row_id,
                "notes": evidence.notes,
            }
        )

    def _add_payroll(self, slots: list[Slot], punches: dict[Slot, _Punch]) -> None:
        last_day = self.config.start_date + timedelta(days=self.config.days - 1)
        for offset in range(0, self.config.days, PAY_PERIOD_DAYS):
            period_start = self.config.start_date + timedelta(days=offset)
            period_end = min(period_start + timedelta(days=PAY_PERIOD_DAYS - 1), last_day)
            for index, staff in enumerate(self.staff):
                daily = [
                    _hours(punches[slot])
                    for slot in slots
                    if slot[0] == index and period_start <= slot[1] <= period_end
                ]
                regular, overtime, gross = _pay(daily, staff.hourly_rate)
                self.data.payroll_runs.append(
                    {
                        "id": self._new_id(),
                        "company_id": self.company_id,
                        "employee_id": staff.id,
                        "period_start": period_start,
                        "period_end": period_end,
                        "regular_hours": regular,
                        "overtime_hours": overtime,
                        "gross_pay": gross,
                    }
                )
