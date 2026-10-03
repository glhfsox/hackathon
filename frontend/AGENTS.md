# frontend/AGENTS.md — rules for work inside `frontend/`

Adds to the root `AGENTS.md`; it does not repeat it. Read before writing code here:
- [`contracts/http-api.md`](../contracts/http-api.md): the `/api/*` endpoints and the `control` field the playground renders
- [`contracts/models.md`](../contracts/models.md): TypeScript types mirror it
- [`docs/architecture.md`](../docs/architecture.md) §9: the pages and their data sources

## Scope

The dashboard (security posture, blocked threats, budget usage, latency overhead), the audit log view and export, the policy editor, and the chat playground that shows which checks fired.

## Rules

- **All HTTP goes through the single API client module.** Components never call `fetch` directly.
- **Request and response types follow `contracts/`.** Do not hand-write shapes that differ from it.
- **The frontend makes no security decisions.** It shows what the backend decided. Policy validation errors come from the backend and are displayed as returned.
- **Keep it simple:** React state and hooks only, no state-management or UI-kit library unless the team agrees.
- **Demo-first:** every screen must work against the running backend, with no mocked data left in the build.

## Commands

Run from `frontend/`:

```bash
npm run lint && npm run build
```

The dev server command and backend URL variable are set when the frontend is first scaffolded. Update this section then.
