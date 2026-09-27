---
name: testing
description: Use when writing or fixing tests in this repo — pytest layout, test database, fixtures, FakeLLM, eval tests, vitest and Playwright for the frontend.
---

# Testing conventions

## Layout
```
backend/tests/unit/          pure logic (rules, masking, parsers). No DB, no network. Fast.
backend/tests/integration/   real Postgres (docker compose test DB or testcontainers), API via httpx AsyncClient.
backend/tests/security/      tenant isolation, role checks, "cannot write source data", PII not sent to LLM.
mcp_server/tests/            tool behavior + permission tests.
frontend/src/**/*.test.tsx   vitest + Testing Library.
frontend/e2e/                Playwright smoke tests.
```

## Rules
- TDD: write the failing test first, see it fail for the right reason, then implement.
- One behavior per test. Name: `test_<unit>_<condition>_<expected>`.
- Arrange / Act / Assert blocks. No logic (loops, ifs) in tests; use `pytest.mark.parametrize`.
- Imports at top. Shared fixtures in the nearest `conftest.py`.
- Never call a real LLM in `make test`. Use `FakeLLM` (scripted responses, records every request for asserts).
  Real-model runs belong in `make eval`.
- Integration tests: each test in a transaction rolled back at the end, or a fresh schema per session.
- Seed data: use the seeded generator with a fixed seed; build small factories for unit tests.
- Security tests are required whenever a slice touches data access, tools, auth, or LLM input.
- Coverage target: `backend/app` ≥ 85%; `rules/` and `security/` 100% of branches.

## Commands
```
uv run pytest -q                       # all backend
uv run pytest backend/tests/unit -q    # fast loop
cd frontend && npm test                # vitest
cd frontend && npx playwright test     # e2e
```
