import uuid
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RuleFinding:
    rule_id: str
    rule_version: str
    employee_id: uuid.UUID
    occurred_on: date
    variant: str
    severity: str
    summary: str
    evidence: dict[str, str]

    @property
    def dedup_key(self) -> str:
        return f"{self.rule_id}:{self.employee_id}:{self.occurred_on.isoformat()}:{self.variant}"
