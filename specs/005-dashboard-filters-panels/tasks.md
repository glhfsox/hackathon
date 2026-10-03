# Tasks: Dashboard filters and resizable panels

## Setup
- [x] T001 Create dev-derived branch and spec/design/checklist in specs/005-dashboard-filters-panels/.
- [x] T002 Add browser test dependency/configuration in frontend/package.json and frontend/playwright.config.ts; commit dependencies separately.

## US1 — Shared editable filters
- [x] T003 [US1] Write filter, export, validation and delayed-response browser cases in frontend/tests/workspace.spec.ts and establish baseline failures.
- [x] T004 [US1] Implement duration parsing/control in frontend/src/ranges.ts and frontend/src/components/TimeFilter.tsx.
- [x] T005 [US1] Guard obsolete polling responses in frontend/src/hooks.ts.
- [x] T006 [US1] Integrate shared window in frontend/src/App.tsx, frontend/src/pages/Overview.tsx and frontend/src/pages/AuditLog.tsx.

## US2 — Resizable workspace
- [x] T007 [US2] Write pointer/keyboard/minimum-size/composer browser cases in frontend/tests/workspace.spec.ts.
- [x] T008 [US2] Implement accessible measured split panels in frontend/src/components/SplitPane.tsx.
- [x] T009 [US2] Integrate sidebar, Audit/Playground splits and multiline composer in frontend/src/App.tsx, frontend/src/pages/AuditLog.tsx, frontend/src/pages/Playground.tsx and frontend/src/index.css.

## Verification and delivery
- [x] T010 Run browser tests, lint/build, and attempt live-backend browser smoke; record results in specs/005-dashboard-filters-panels/validation.md.
- [x] T011 Commit the feature branch and prepare a dev-targeted PR description in specs/005-dashboard-filters-panels/pr-description.md.

Setup → test baseline → US1 → US2 → verification. Stories use shared App/CSS files, so implementation is sequential. Spec-kit research alone was delegated read-only. No extension hooks are configured. Checklist: 6 satisfied, 0 unchecked.
