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
npm run dev        # http://localhost:5173 (the only origin the backend's CORS allows)
```

Settings live in `.env.local` (names in `.env.example`): `VITE_API_URL` (default `http://localhost:8000`), `VITE_PLAYGROUND_API_KEY` (must equal the backend's `PLAYGROUND_API_KEY`), `VITE_PLAYGROUND_MODEL` (default `gemma4`).

Known issue: Vite 8 (rolldown) cannot resolve `node_modules` when the project path contains non-ASCII characters (e.g. a Cyrillic folder name). Work from an ASCII path.
