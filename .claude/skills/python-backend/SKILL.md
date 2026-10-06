---
name: python-backend
description: Use when writing or changing Python backend code in this repo — FastAPI routes, services, SQLAlchemy models, Alembic migrations, Postgres roles/RLS, MCP server tools, settings.
---

# Python backend conventions

## Layers (dependencies point down only)
```
api/        FastAPI routers. Parse input, call a service, map errors to HTTP. No SQL, no business logic.
services/   Business logic. Takes a Session + TenantContext. Used by API AND mcp_server.
rules/      Pure detection functions. No DB, no I/O. Input: dataclasses/Pydantic. Output: list[Finding].
graph/      LangGraph graphs. Call services and llm/, never the DB directly.
security/   Auth, tenant context, PII masking, injection wrappers.
db/         models.py, session.py, repositories (query functions).
```
`mcp_server/` tools are thin: validate args → call a service → return a Pydantic model.

## Style
- Imports at top, grouped stdlib / third-party / local. ruff sorts them.
- `from __future__ import annotations` not needed (3.12). Use `X | None`, `list[int]`.
- Pydantic v2 models for all API I/O and tool I/O. SQLAlchemy 2 typed `Mapped[...]` models.
- Domain errors in `app/errors.py` (`NotFoundError`, `ForbiddenError`, `ValidationError`); `api/` maps them.
- Settings: `Settings(BaseSettings)` in `app/config.py` (reads `.env`) for the API, seed and migrations,
  injected via `Depends`. The MCP process has its own standalone `McpSettings` (`mcp_server/settings.py`) that
  reads only `.env.mcp` and never imports `app.config`, so it never holds the admin `DATABASE_URL`.
- Sync or async: use async FastAPI + async SQLAlchemy (`asyncpg`) consistently. Don't mix.
- Logging: `structlog`, JSON, include `company_id`, `request_id`. Never log PII or secrets.

## Database
- Every change = Alembic migration (`alembic revision --autogenerate -m "..."`, then review by hand).
  Migration must downgrade cleanly. Accepted exception: `0176fb6e4a65` (the `mcp_server` login) downgrades
  as a no-op, because roles are cluster-wide and other databases and the developer's MCP login rely on it.
- Every tenant table has `company_id` NOT NULL + index leading with it.
- Timestamps: `timestamptz`, UTC. Money: `numeric(12,2)`. Never float for money or hours.
- Roles: `app_rw` (API), `mcp_reader` (SELECT on source tables, INSERT on findings/proposed_corrections/audit_log),
  both NOLOGIN. `mcp_server` is the MCP login: LOGIN NOINHERIT, member of `mcp_reader` with INHERIT FALSE, SET TRUE
  only (no privileges until `SET LOCAL ROLE mcp_reader`). Its password is set by `make mcp-user`, never in a migration.
- RLS (slice 10+): policy `company_id = current_setting('app.company_id')::uuid`; set it per transaction with
  `SET LOCAL app.company_id = ...` in the session dependency. Tables use `FORCE ROW LEVEL SECURITY`.
- Detection SQL: prefer window functions / CTEs over Python loops for aggregation; keep rules pure on the result.

## API
- Routes under `/api/v1`. Plural nouns. Pagination: `limit` + `cursor`.
- Writes that change source data exist only behind approval endpoints and write `audit_log` in the same transaction.
