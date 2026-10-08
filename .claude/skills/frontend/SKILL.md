---
name: frontend
description: Use when building or changing the React dashboard in frontend/ — components, data fetching, auth, SSE chat, tests, formatting.
---

# Frontend conventions

## Stack
React + Vite + TypeScript (strict), TanStack Query for server state, React Router (`react-router`), Tailwind v4
(Tailwind, chosen in slice 7: `@tailwindcss/vite` plugin + `@import 'tailwindcss'` in `src/index.css`;
utility classes in JSX, no CSS modules), vitest + Testing Library, Playwright. Prettier + ESLint (`npm run format`, `npm run lint`).

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

## Styling
- Colors come from theme tokens in `src/index.css`: `:root` holds the light values and `@media (prefers-color-scheme: dark)` the dark ones (`--wg-*` variables), exposed to Tailwind through the `@theme inline` block (`bg-surface`, `text-ink`, `border-line`, ...). Add new colors there in both schemes; do not hard-code hex values in JSX.
- Form controls and buttons use the `border-field-line` token (not `border-line`) so the border stays visible against the surface.
- Severity colors use the `low|medium|high|neutral` token families; danger and caution use `danger-*` and `caution-*`.
- Shared UI primitives in `src/components/`: `Card`, `Badge`, `Callout`, `PageHeader`, `BackLink`, `NotFoundState`, `ShortId`, `icons`. Reuse them before adding new markup.
- Animations are CSS-only (`--animate-*` in `src/index.css`) and must be disabled under `@media (prefers-reduced-motion: reduce)`.

## Tests
Component tests next to the component (`*.test.tsx`). One Playwright smoke test per main flow.
