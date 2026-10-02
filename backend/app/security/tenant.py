import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    """The one company a request may see. Every service query filters by it."""

    company_id: uuid.UUID
