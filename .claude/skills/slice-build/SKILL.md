---
name: slice-build
description: Use when building, continuing, or reviewing any slice from docs/PLAN.md ("build slice N", "next slice"). Gives the token-lean workflow, the done checklist, and the slice doc format.
---

# Slice build workflow

## 1. Read ONLY this (token budget)
- `CLAUDE.md` (already loaded) = the rules.
- Your slice section of `docs/PLAN.md`: `Grep "### Slice N " docs/PLAN.md -n`, then Read with offset/limit
  up to the next `### Slice`. Never read the whole plan.
- Do NOT read `docs/slices/README.md`, other slice docs, or the rest of the plan unless the slice text names
  something you cannot find with Grep in the code. Then read only that one section/doc.
- Load other project skills only if the slice needs them: `python-backend`, `testing`, `llm-agents`, `frontend`.

## 2. Plan in 5 lines max
Files to add/change, tests to write, blockers (ask the user).

## 3. Build (TDD, vertical)
- Failing test → pass → refactor, per behavior. Stay inside slice scope; extras go to "Known limits".

## 4. Done checklist (all required)
```
make fmt     # ruff format . + ruff check --fix . + frontend format
make lint
make test
make eval    # only if slice touches rules, LLM, RAG, chat, or security
```
Every "Done when" item is proven by a test or command output.

## 5. Write the slice doc (no need to read any template)
Create `docs/slices/slice-NN-<kebab-name>.md` (NN zero-padded) with exactly these sections:

```
# Slice NN — <Name>
**Status:** done · **Date:** YYYY-MM-DD
## Goal                     1-2 sentences
## What was built           table: Area | Files | What it does
## How it works             short flow; mermaid if 3+ steps
## Key decisions            table: Decision | Why | Alternatives rejected
## Data / schema changes    or "None"
## Security notes           which guidelines apply and how enforced
## Tests                    table: Test file | What it proves; then real make lint/test/eval output lines
## How to run / try it      exact commands
## Known limits / follow-ups
```

Then append one row to the log without reading it:
```
echo "| NN | [slice-NN-<name>](slice-NN-<name>.md) | <one-line summary> |" >> docs/slices/README.md
```

## 6. Report to the user
What was built, lint/test/eval results, doc path, decisions they must make.
