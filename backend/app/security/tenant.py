import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    company_id: uuid.UUID
