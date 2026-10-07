from dataclasses import dataclass, fields
from datetime import date

DAYS_PER_WEEK = 7


class SeedConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SeedConfig:
    seed: int = 42
    start_date: date = date(2026, 1, 4)
    days: int = 60
    employees_per_company: int = 200
    overtime_breaches_per_company: int = 12
    buddy_pairs_per_company: int = 3
    buddy_days_per_pair: int = 4
    off_shift_accesses_per_company: int = 10
    injected_notes_per_company: int = 5

    def __post_init__(self) -> None:
        negative = [
            item.name
            for item in fields(self)
            if item.name != "seed"
            and isinstance(getattr(self, item.name), int)
            and getattr(self, item.name) < 0
        ]
        if negative:
            raise SeedConfigError(f"Counts must not be negative: {', '.join(negative)}.")
        if self.employees_per_company < 1:
            raise SeedConfigError("Each company needs at least one employee.")
        if self.days < DAYS_PER_WEEK:
            raise SeedConfigError("Seed at least one full week so every weekday occurs.")
