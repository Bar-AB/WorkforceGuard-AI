---
name: frontend
description: Use when building or changing the React dashboard in frontend/ — components, data fetching, auth, SSE chat, tests, formatting.
---

# Frontend conventions

## Stack
React + Vite + TypeScript (strict), TanStack Query for server state, React Router, Tailwind (or CSS modules — pick once in slice 7 and record it),
vitest + Testing Library, Playwright. Prettier + ESLint (`npm run format`, `npm run lint`).

## Structure
```
src/api/          typed client functions + TanStack Query hooks (one file per resource)
src/features/     findings/, approvals/, chat/, feedback/ — each has components, hooks, tests
src/components/   shared UI only
src/auth/         OIDC (Keycloak) wiring, route guards
```

## Rules
- Imports at top. Function components + hooks only. No `any`.
- API types match backend Pydantic models (generate from OpenAPI with `openapi-typescript` once the API exists).
- Every data view handles loading, empty, and error states.
- Never render raw HTML from data or LLM output (no `dangerouslySetInnerHTML`). LLM text is plain text or sanitized markdown.
- Role checks in UI are convenience only; the API enforces them.
- Chat: consume SSE with `EventSource`/fetch stream; render `token`, `tool_start`, `tool_end`, `error` events.
- Accessibility: labeled inputs, keyboard-usable tables and buttons.

## Tests
Component tests next to the component (`*.test.tsx`). One Playwright smoke test per main flow.
