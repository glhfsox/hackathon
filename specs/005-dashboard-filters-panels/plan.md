# Implementation Plan: Dashboard filters and resizable panels

**Branch**: `feat/dashboard-filters-panels` | **Date**: 2026-10-03 | **Spec**: [spec.md](spec.md)

## Summary

Share App-owned duration state across Overview/Audit, with active presets and validated editable duration. Add one accessible split-panel component for traces, and a multiline Playground composer. Guard polling against stale responses. Existing [HTTP contracts](../../contracts/http-api.md) already support since; no API changes.

## Technical Context

- TypeScript strict, React 19, Vite 8; existing state/hooks and CSS.
- No runtime dependencies or new backend behavior.
- Test-only Playwright uses installed Google Chrome or managed Chromium. Browser request fixtures are isolated test data, never shipped UI mocks.
- Desktop browser workspace; pointer and keyboard separators with bounded dimensions that respond to viewport changes.
- Component state for sizes, retained while mounted. Persistence across reloads is outside scope.

## Constitution Check

Pass before and after design: frontend only displays backend decisions; all HTTP stays in the API client. Existing contracts remain unchanged. Meaningful interaction tests plus lint/build prove the scoped UI changes. Explicit user instruction selects dev as base.

## Project Structure

- `src/ranges.ts`, `components/TimeFilter.tsx`: parsed applied duration and editable controls.
- `src/hooks.ts`: polling generation guards and reset on query changes.
- `src/components/SplitPane.tsx`: pointer/keyboard shared dividers and viewport clamping.
- `src/App.tsx`, `pages/Overview.tsx`, `pages/AuditLog.tsx`, `pages/Playground.tsx`, `index.css`: integration and theme.
- `playwright.config.ts`, `tests/workspace.spec.ts`: repeatable browser checks.

## Verification

Write browser cases first, run to establish expected failures, implement the tasks, then run npm test, npm run lint, npm run build. Inspect browser screenshots with test-only route fixtures. The user explicitly wants frontend testing only; do not start or connect a backend. Keep the dev server available for the user's own test. Commit dependency changes separately.

Follow-up: remove the directory/sidebar split from App, enlarge the branded header, remove clock/status-path clutter, and replace bare Playground placeholders with guidance. Keep the existing four-tab navigation and remaining split panels.
