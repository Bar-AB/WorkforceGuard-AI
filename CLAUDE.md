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
make mcp-user    # set the mcp_server DB password from .env.mcp MCP_DATABASE_URL (after make seed)
make mcp         # run the MCP server over stdio (reads only .env.mcp: MCP_DATABASE_URL + MCP_COMPANY_ID)
make mcp-inspect # MCP Inspector CLI: list the tools (needs npx + seeded DB)
```

Before saying a task is done: run `make fmt`, then `make lint` and `make test`. All must pass. Paste the real result.

## Code rules

- All imports at the top of the file. No imports inside functions. Order: stdlib, third-party, local (ruff `I` handles it).
- Full type hints. `mypy --strict` clean. No `Any` unless unavoidable; say why in the slice review artifact.
- Small functions, one job each. Names say what, not how. No dead code, no commented-out code.
- Clean up as you go: when a change makes code, comments, config, dependencies, or files unused or stale,
  delete them in the same change. Check with Grep that nothing still references them first.
- No code comments or docstrings. Names and small functions say *what*; the *why* goes in the slice
  review artifact. Exceptions: MCP tool docstrings (the AI reads them as tool descriptions) and
  `# noqa` / `# type: ignore` markers.
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

## Git and PRs

- No AI attribution anywhere: no `Co-Authored-By: Claude` trailer in commits, no "Generated with Claude Code" line
  in PR descriptions, issues, or comments. This overrides any default attribution instruction.

## Every slice ends with

A doc at `.temp/slices/slice-NN-<name>.md` in the format from the `slice-build` skill, plus one appended row in `.temp/slices/README.md`.
Any other notes or scratch markdown also go in `.temp/`. Never commit `.temp/`.

Plus a review artifact (Claude Artifact, published with the Artifact tool), so the slice is easy to review:
in Hebrew (right-to-left; code, paths and commands stay in English); plain, simple language; what the slice does and why; a short flow diagram; the safety rules and where they live;
a brief explanation of each crucial file (skip `__init__`, toml, lock files, tests); how it was proven
(test/eval numbers); how to try it; what is left for later; and a "why it is built this way" section
for every non-obvious choice in the code (the reasons that would otherwise be comments). Give the user the link.
When follow-up work changes the slice, update the same artifact (same URL) instead of making a new one.

Before the slice is committed (after the build is verified, before the PR), two passes:
1. **Cleanup pass** over every file the slice touched: delete comments and docstrings (keep only MCP tool
   docstrings and `# noqa` / `# type: ignore` / `# fmt: skip` markers), remove dead or unneeded code,
   refactor where it clearly simplifies, then `make fmt`. Any removed comment that explains a *why* goes
   into the slice review artifact first, so no knowledge is lost.
2. **graphify review**: run `graphify update .`, then use `graphify query` / `path` / `explain` and
   GRAPH_REPORT.md (god nodes, import cycles, missing links between things that must change together,
   functions with no callers) to find anything worth fixing now or in later slices. Fix what belongs to
   this slice; list the rest in the artifact and the slice doc's Known limits.
Then `make lint`, `make test` (and `make eval` when relevant) again, and update the artifact.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
