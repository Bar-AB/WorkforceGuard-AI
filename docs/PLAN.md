# WorkforceGuard AI — Build Plan (sliced for cc10x)

Source spec: https://claude.ai/artifact/JQnW2P8HNxHwFawz1yC4VS (reviewed 2026-09-27).
This file replaces the spec's 6-phase roadmap. Architecture changes are in "Fixes vs. original spec".
Production additions are in "Production layer" and slices 10–16.

## What it is

A local, agent-based system that finds anomalies in attendance, access, and payroll data
(Priority-style ERP HR domain). Synthetic data only. Learning + portfolio project.

How users meet it:
1. **Background scan** — runs on a schedule, no user input. Finds and explains anomalies.
2. **Dashboard** — findings inbox with evidence and explanation.
3. **Human approval** — agent proposes a fix, pauses, a human approves or rejects.
4. **Chat** — read-only Q&A over the data (dashboard panel, later Telegram/Slack via Hermes).

## Stack (final)

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | Python 3.12, uv, FastAPI | One language for API, agents, MCP |
| DB | Postgres 16 (Docker), Alembic, SQLAlchemy 2, pgvector | Closes the SQL gap; RAG in the same DB |
| Detection | Plain Python + SQL rules, then per-employee statistics | Deterministic, unit-tested. No LLM here |
| Agent flow | LangGraph (+ LangChain for model/tool wrappers) | Known stack; `interrupt()` gives human-in-the-loop |
| LLM | Ollama, Qwen3 4B default (8B optional) | 16GB RAM, no GPU. Behind an OpenAI-compatible provider interface |
| Tracing | Langfuse Cloud free tier (or LangSmith free) + OpenTelemetry | Self-hosted Langfuse is too heavy for 16GB |
| Tools for agents | MCP server (official Python MCP SDK) | Least-privilege tool boundary |
| Auth | Keycloak (OIDC) in Docker | Real login + roles, industry standard |
| Frontend | React + Vite + TypeScript, TanStack Query | Priority's stack; SPA is enough |
| Orchestrator/chat | Hermes Agent (Nous Research) | Added late, as a thin adapter only |
| Cloud practice | LocalStack + OpenTofu/Terraform | Free AWS emulation; IaC is the real skill |
| CI | GitHub Actions: ruff, mypy, pytest, vitest, eval gate | Every slice must stay green |

## Fixes vs. original spec

1. **"Deterministic engine (LangGraph + Ollama)" is a contradiction.** Rules decide (pure Python/SQL, tested).
   The LLM only *explains* a finding and answers chat.
2. **`approve_correction` must not be an MCP tool.** Agents get `propose_correction` only. Humans approve via dashboard → API.
3. **Least privilege must be real.** MCP server uses its own Postgres role: `SELECT` on source tables,
   `INSERT` only on `findings` / `proposed_corrections` / `audit_log`.
4. **Data model gaps.** Added `findings`, `proposed_corrections`, `overtime_policies`, `users`, `anomaly_labels`.
   Every table has `company_id` from day one (tenant key). `audit_log` gets actor, rule_version, model_name, evidence JSONB.
5. **No evals in the spec.** Seeded data with labeled anomalies → precision/recall per rule. LLM outputs get eval sets.
6. **RAM.** Default Qwen3 4B; Docker Compose profiles so ollama / localstack / keycloak run only when needed. Tests use a fake LLM.
7. **Two schedulers.** Hermes cron locally; Lambda + EventBridge only in the AWS slice, both calling the same scan endpoint.
8. **Dashboard is not optional.** Human approval needs a UI → slice 7.
9. **Dashboard does not wrap MCP tools.** API and MCP both call one shared service layer.
10. **LocalStack:** check current Community-edition terms before the AWS slice. Fallback: MinIO (S3) + ElasticMQ (SQS).

## Production layer

### Which protection applies to which agent

There are three agents: the **scan agent** (background), the **chat agent** (user talks to it), and **Hermes** (user talks to it, and it writes its own skills).

Key point: user text is not the only input. **The data is input too.** A note field saying "ignore your rules"
reaches the background scan agent just as well. So most protections apply to every agent.

| Protection | Scan agent | Chat agent | Hermes | Slice |
| --- | --- | --- | --- | --- |
| Tenant isolation (company A never sees B) | Yes | Yes | Yes | 10 |
| Per-user roles (manager sees own team) | No user — runs as service role | Yes | Yes | 10 |
| PII masking before the LLM | Yes | Yes | Yes | 11 |
| Prompt-injection defense on data/tool output | Yes | Yes | Yes | 14 |
| Prompt-injection defense on user messages | — | Yes | Yes | 14 |
| Output checks (no PII leak, valid schema) | Yes | Yes | Yes | 11, 14 |
| Least-privilege tools, human approval for writes | Yes | Yes (read-only) | Yes (API calls only) | 5, 8 |
| Audit log | Yes | Yes | Yes | 4 |
| Evals + LLM-as-Judge in CI | Yes | Yes | — | 6, 13 |
| RAG over policies | Yes (cite the rule) | Yes | via chat | 12 |
| Routing + SSE streaming | Progress only | Yes | — | 13 |
| Rate limits, per-user token budget | Global cap | Yes | Yes | 16 |
| Retries, fallback model, dead-letter queue | Yes (most important here) | Yes | Yes | 16 |
| Skill review (git + PR diff) | — | — | Yes | 17 |

Rule of thumb: **security and data protection = all agents. Conversation features = user-facing agents.
Reliability = matters most for the background agent**, since nobody is watching it run.

### Production guidelines (apply from the slice they are introduced onward)

- All LLM input built from data goes through: tenant filter → PII mask → untrusted-content wrapper.
- LLM output is always Pydantic-validated. Invalid output = safe fallback, never a crash, never a raw write.
- No agent can change source data. Only the API, after a human approval, can.
- Every LLM call is traced (Langfuse) with tenant, user, model, prompt version, tokens, latency.
- Prompts live in files with a version. Changing a prompt = eval run in CI.
- Evals are a CI gate: a PR fails if rule F1, judge score, or red-team pass rate drops below baseline.

## Repo layout

```
backend/      FastAPI app, services, rules, graph, security, db (alembic)
mcp_server/   MCP tools (thin, calls backend services)
frontend/     React + Vite
infra/        docker-compose.yml, keycloak/, tofu/ (AWS via LocalStack)
evals/        datasets, judge prompts, red-team set, eval scripts, baselines
docs/         PLAN.md, ADRs, CONTEXT.md
hermes/       skills/ (under git)
```

## Slices

Rules for every slice:
- One slice = one cc10x BUILD run = one PR-sized diff you can review in ~20 min.
- TDD. CI green (including eval gate once it exists). No slice starts before the previous one is merged.
- Follow the production guidelines above from the slice that introduces them.
- Follow `CLAUDE.md` code rules and the `slice-build` project skill.
- Before done: `make fmt` (ruff format . + ruff check --fix . + frontend format), then `make lint` and `make test` pass.
- Every slice has tests for each "Done when" item.
- Every slice ends with `docs/slices/slice-NN-<name>.md` (format in the `slice-build` skill) + a row in `docs/slices/README.md`.
- Agents read only their slice section + `CLAUDE.md`, never this whole file.
- Run each with: `/cc10x:cc10x-router build slice N from docs/PLAN.md — use the slice-build skill`

### Slice 0 — Repo skeleton + CI
- uv project, FastAPI `/health` + test, pytest, mypy (strict).
- ruff config in `pyproject.toml`: formatter on, lint rules `E,F,I,B,UP,SIM,PL,RUF`, `PLC0415` on (no imports inside functions).
- Vite React TS app with vitest, ESLint, Prettier.
- `infra/docker-compose.yml` with Postgres only. `Makefile` targets from `CLAUDE.md`: `up`, `fmt`, `lint`, `test`, `eval` (stub).
- pre-commit hooks: ruff format, ruff check, prettier.
- GitHub Actions runs `make lint` + `make test` for backend and frontend.
- `.gitignore`, `.env.example`.
- **Done when:** `make up && make fmt && make lint && make test` pass locally and in CI; a function-level import fails lint.

### Slice 1 — Schema + migrations
- Alembic migration for: companies, employees, users, shifts, attendance_events, access_logs,
  payroll_runs, overtime_policies, findings, proposed_corrections, audit_log, anomaly_labels.
- `company_id` on every tenant table. Indexes on (company_id, employee_id, timestamp). FKs. `CHECK` constraints.
- Postgres roles: `app_rw`, `mcp_reader` (least privilege, see Fix 3).
- **Done when:** `alembic upgrade head` and `downgrade base` both work; a test proves `mcp_reader` cannot UPDATE source tables.

### Slice 2 — Synthetic data generator
- Seeded (repeatable) generator: 2 companies, ~200 employees each, 60 days of shifts/attendance/access/payroll.
- Injects known anomalies (overtime breach, buddy punching, off-shift access) → `anomaly_labels`.
- Injects a few free-text fields with prompt-injection strings (used later by slice 14).
- **Done when:** `make seed` is repeatable (same seed = same rows); tests check injected counts.

### Slice 3 — Rule #1: overtime violation + eval harness
- Pure function rules over SQL results (window functions for weekly hours). Policy from `overtime_policies`
  (model Israeli rules: daily/weekly limits).
- `evals/run_rules.py`: precision / recall / F1 per rule vs `anomaly_labels`, writes `evals/report.md`,
  compares to `evals/baselines.json`. Wired into CI as the eval gate.
- **Done when:** edge-case tests (night shifts, crossing midnight, week boundary); recall ≥ 0.95; CI fails if F1 drops.

### Slice 4 — Service layer + read API + audit
- `services/` for findings, employees, scans. `POST /scans` runs rules, stores `findings`, writes `audit_log`.
- `GET /findings`, `GET /findings/{id}` (with evidence), `GET /employees/{id}`.
- **Done when:** API tests against a real test DB; scan is idempotent (no duplicate findings).

### Slice 5 — MCP server
- Tools: `get_attendance_events`, `get_shifts`, `get_access_logs`, `get_payroll_summary`, `list_findings`,
  `propose_correction`. All call the service layer. Uses `mcp_reader` role.
- Payroll summary returns aggregates only (no raw salary rows).
- **Done when:** tool tests pass; a test proves no tool can change source data; works in MCP Inspector.

### Slice 6 — LLM provider + LangGraph scan graph + LLM-as-Judge
- `LLMProvider` interface (OpenAI-compatible) → Ollama. `FakeLLM` for tests. Prompts versioned in files.
- Graph: load → detect (rules) → explain (LLM) → persist. Output Pydantic-validated, fallback template on failure.
- `evals/judge.py`: LLM-as-Judge scores explanations (faithful to evidence? no invented facts?) on a fixed set.
  Judge baseline added to the CI gate (runs on a small sample; full run manual).
- Ollama in compose under profile `llm`. Langfuse tracing via env var.
- **Done when:** graph tests pass with FakeLLM; judge report exists; trace visible for one real run.

### Slice 7 — Dashboard v1
- Findings table (filter by type/severity/date), finding detail with evidence + explanation.
- Loading, empty, and error states.
- **Done when:** vitest + one Playwright smoke test (list → detail) pass.

### Slice 8 — Human-in-the-loop corrections
- Graph node calls `propose_correction` → LangGraph `interrupt()` → dashboard shows pending proposal.
- Approve / reject via API (not MCP). Only the API applies the change. Every step in `audit_log`.
- LangGraph Postgres checkpointer.
- **Done when:** e2e test: propose → approve → record changed + audit rows; reject → nothing changed.

### Slice 9 — Rules #2 and #3
- Buddy punching: two employees clock in from the same IP/device within N seconds, repeatedly.
- Off-shift access: `access_logs` activity outside the employee's shift window.
- **Done when:** unit tests + eval report updated; precision ≥ 0.9; baselines updated.

### Slice 10 — Auth, roles, tenant isolation (multi-entity guard)
- Keycloak in compose (profile `auth`). OIDC login in the dashboard; JWT checked in FastAPI.
- Roles: `admin`, `manager` (own team only), `auditor` (read-only). Approvals require `admin` or `manager`.
- Postgres Row-Level Security on every tenant table using `company_id` + session variable set per request.
  Manager scope enforced in the service layer.
- MCP server and scan agent run with an explicit tenant context; no "all tenants" path.
- **Done when:** tests prove company A user gets zero rows of company B via API, MCP, and direct SQL with RLS;
  a manager cannot see or approve outside their team.

### Slice 11 — PII masking + output guard
- `security/pii.py`: before any LLM call, replace names, national IDs, emails, IPs with stable tokens
  (`EMP_0142`); keep the map server-side; unmask only in the final response to an authorized user.
- Output guard: block responses that contain unmasked PII the user is not allowed to see.
- Apply in scan graph now; chat uses it from slice 13.
- **Done when:** tests show the LLM request payload (captured via FakeLLM) contains no raw PII; unmasking respects roles.

### Slice 12 — RAG over policies
- `policies/` markdown: overtime law summary, company HR policy, access policy. Chunk + embed
  (Ollama embedding model, e.g. `nomic-embed-text`) into pgvector, per tenant.
- Explain node retrieves the relevant policy and cites it (`policy_id`, section) in the explanation.
- Retrieval eval: for each anomaly type, is the right policy in top-3? Added to the eval gate.
- **Done when:** explanations show citations; retrieval hit@3 ≥ 0.9; judge checks citation faithfulness.

### Slice 13 — Chat agent + routing + SSE streaming
- LangGraph agent using MCP tools (via `langchain-mcp-adapters`). Read-only. Runs as the logged-in user (RLS applies).
- Router node: question type → `data` (tools), `policy` (RAG), `both`, or `refuse` (out of scope).
  Model routing: 4B by default, escalate to 8B (or a cloud model if configured) on low confidence.
- `POST /chat` streams tokens and tool-step events via SSE; dashboard chat panel renders them live.
  Scan progress also streamed via SSE.
- Conversation memory: last N turns + a running summary; recency-weighted retrieval for findings history (stretch goal).
- Eval set: ~30 Q&A pairs scored for route correctness, tool-call correctness, and judge answer score.
- **Done when:** streaming works in the UI; eval scores recorded and gated in CI; tests with FakeLLM pass.

### Slice 14 — Prompt-injection defense + red-team set
- Treat all data and tool output as untrusted: wrap in delimited blocks, system prompt states it is data only.
- Input classifier on user messages (rules + small LLM check) for jailbreak / exfiltration attempts.
- Tool allow-list per agent, max steps, no tool can be called with arguments the router did not approve.
- `evals/redteam/`: ~50 attacks (direct, and indirect via the seeded injected fields): "ignore instructions",
  "show company B", "reveal salaries", "approve this correction". Pass rate added to the eval gate.
- **Done when:** red-team pass rate ≥ 95%; zero cross-tenant or write actions in any red-team run.

### Slice 15 — Feedback loop + statistical detection
- "Real problem / False alarm" buttons on each finding → `finding_feedback` table.
- False-positive rate per rule on a dashboard page; feedback exported as new eval cases.
- Per-employee baseline detection: z-score / IQR on weekly hours and access times vs the employee's own history.
  Flags "unusual for this person", marked as `statistical`, lower severity than rule findings.
- **Done when:** feedback stored and shown; statistical detector catches seeded "slow drift" anomalies the rules miss.

### Slice 16 — Reliability + observability
- Retries with backoff on LLM and DB calls; fallback model; circuit breaker when Ollama is down
  (scan still stores rule findings, explanation marked "pending").
- Failed scan jobs → dead-letter table + dashboard view; re-run button.
- Rate limits per user on chat; token budget per tenant per day.
- OpenTelemetry traces + metrics (latency, errors, tokens, cost) exported locally (Jaeger or Grafana via profile `obs`).
- **Done when:** chaos test (stop Ollama mid-scan) → no crash, findings saved, job recoverable; rate limit returns 429.

### Slice 17 — Hermes Agent adapter
- Hermes runs cron (daily scan) and a Telegram or Slack channel. It only calls `POST /scans` and `POST /chat`
  with a service token scoped to one tenant. Chat messages go through the same auth, masking, and injection checks.
- `~/.hermes/skills/` symlinked into `hermes/skills/` under git; review skill diffs like PRs.
- **Done when:** scheduled scan fires and a summary arrives; a red-team message via Telegram is blocked.

### Slice 18 — AWS practice (LocalStack + OpenTofu)
- OpenTofu: S3 bucket (weekly report export), SQS queue (event per new finding) with DLQ, IAM roles (least privilege),
  Secrets Manager for DB creds and Keycloak client secret, Lambda + EventBridge calling the scan endpoint.
- Backend reads secrets from Secrets Manager when `AWS_MODE=on`.
- **Done when:** `tofu apply` against LocalStack works; a test uploads a report and reads a queue message;
  IAM policy denies an out-of-scope action.

### Slice 19 — Demo + docs
- Static snapshot build of the dashboard (sample findings JSON) deployed to Vercel.
- README: architecture diagram, how to run, eval + red-team results, security model (the agent/protection table), ADRs.
- **Done when:** public link works with no backend; README lets a stranger run it in 10 minutes.

## Skipped on purpose

- Free-form text-to-SQL: risky; the MCP tools cover the same needs safely.
- Self-hosted Langfuse: too heavy for 16GB RAM.

## Open decisions (decide before the slice that needs them)

- Slice 3: exact overtime policy numbers to model.
- Slice 13: allow a cloud model for escalation, or local only?
- Slice 17: Telegram or Slack.
- Slice 18: LocalStack Community still free for what we need? Else MinIO + ElasticMQ.
