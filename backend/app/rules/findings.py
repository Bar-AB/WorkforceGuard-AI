import uuid
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RuleFinding:
    """What a rule detected. Plain data; persisting it is the caller's job."""

    rule_id: str
    rule_version: str
    employee_id: uuid.UUID
    occurred_on: date
    # Tells apart two findings of one rule for the same employee and day.
    variant: str
    severity: str
    summary: str
    evidence: dict[str, str]

    @property
    def dedup_key(self) -> str:
        """Same anomaly, same key, so re-running a scan never stores it twice."""
        return f"{self.rule_id}:{self.employee_id}:{self.occurred_on.isoformat()}:{self.variant}"
