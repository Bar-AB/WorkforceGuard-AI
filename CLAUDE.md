# WorkforceGuard AI

Agent-based anomaly detection for attendance, access, and payroll data (ERP HR domain).
Synthetic data only. Plan: `docs/PLAN.md` (sliced). Slice docs: `.temp/slices/` (git-ignored, local only).

## Save tokens

- Read ONLY your slice section of `docs/PLAN.md` (Grep `### Slice N `, Read with offset/limit). Never the whole plan.
- This file holds all the rules you need. Do not read `.temp/` or other plan sections unless blocked.
- Use Grep/Glob to find code; read only the files you change or call.
- Load the matching project skill instead of re-deriving conventions:
  - `slice-build` — any slice work (start here)
  - `python-backend` — FastAPI, SQLAlchemy, Alembic, Postgres, RLS
  - `testing` — pytest, test DB, FakeLLM, vitest, Playwright
  - `llm-agents` — LangGraph, LLM provider, prompts, evals, PII masking, injection defense
  - `frontend` — React + Vite + TS dashboard

## Stack

Python 3.12 + uv, FastAPI, SQLAlchemy 2, Alembic, Postgres 16 + pgvector, LangGraph, Ollama (Qwen3 4B),
MCP Python SDK, React + Vite + TypeScript, Docker Compose, GitHub Actions.

## Layout

```
backend/      app/ (api, services, rules, graph, security, db), tests/, alembic/
mcp_server/   MCP tools, call backend services only
frontend/     React app
infra/        docker-compose.yml, keycloak/, tofu/
evals/        datasets, judge, redteam, baselines.json
docs/         PLAN.md, slices/, adr/
```

## Commands

```
make up          # start Postgres (and profiles as needed)
make seed        # load the synthetic demo companies (SEED=42; same seed = same rows)
make fmt         # ruff format . && ruff check --fix . && (cd frontend && npm run format)
make lint        # ruff format --check . && ruff check . && mypy backend mcp_server evals && (cd frontend && npm run lint)
make test        # pytest && (cd frontend && npm test)
make eval        # rule F1/recall vs evals/baselines.json (run make seed first)
```

Before saying a task is done: run `make fmt`, then `make lint` and `make test`. All must pass. Paste the real result.

## Code rules

- All imports at the top of the file. No imports inside functions. Order: stdlib, third-party, local (ruff `I` handles it).
- Full type hints. `mypy --strict` clean. No `Any` unless unavoidable, with a comment why.
- Small functions, one job each. Names say what, not how. No dead code, no commented-out code.
- Clean up as you go: when a change makes code, comments, config, dependencies, or files unused or stale,
  delete them in the same change. Check with Grep that nothing still references them first.
- Comments only for *why*, never for *what*.
- No bare `except`. Never swallow errors. Raise domain errors; map them to HTTP in the API layer only.
- Config from env via `pydantic-settings`. No secrets in code or git.
- Tests for every change (TDD: failing test first). Every bug fix gets a regression test.

## Hard rules (security)

- Agents never change source data. Only the API, after human approval, can.
- Every tenant query is scoped by `company_id` (and RLS from slice 10).
- LLM input from data: tenant filter → PII mask → untrusted-content wrapper. LLM output: Pydantic-validated.
- Invalid LLM output = safe fallback, never a crash, never a raw write.
- Prompts are versioned files; changing one = eval run. Every LLM call traced (no raw PII in traces).
- Evals are a CI gate: PR fails if rule F1, judge score, or red-team pass rate drops below `evals/baselines.json`.
- Never commit real personal data. Seed data only.

## Every slice ends with

A doc at `.temp/slices/slice-NN-<name>.md` in the format from the `slice-build` skill, plus one appended row in `.temp/slices/README.md`.
Any other notes or scratch markdown also go in `.temp/`. Never commit `.temp/`.
