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
    severity: str
    summary: str
    evidence: dict[str, str]
