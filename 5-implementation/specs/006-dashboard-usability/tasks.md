# Tasks: Dashboard usability

Input: [plan.md](plan.md), [spec.md](spec.md).

## Phase 1: Setup
- [x] T001 Create branch from origin/dev and inspect frontend rules and contracts in frontend/AGENTS.md.
- [x] T002 Record validated requirements in specs/006-dashboard-usability/spec.md.

## Phase 2: Foundation
- [x] T003 Add categorical rule and AI risk display in frontend/src/components/ui.tsx.

## Phase 3: US1 — Preserve conversations
Independent check: navigate during a request, return, and submit safe follow-up history.
- [x] T004 [US1] Keep playground mounted in frontend/src/App.tsx and limit scroll effects in frontend/src/pages/Playground.tsx.
- [x] T005 [US1] Send with Enter and retain Shift+Enter/composition behavior in frontend/src/pages/Playground.tsx.
- [x] T006 [US1] Validate pending/completed navigation, drafts and blocked history in frontend/tests/workspace.spec.ts.

## Phase 4: US2 — Explain overview
Independent check: OWASP removal, percentile meanings and standalone usage are visible.
- [x] T007 [US2] Remove OWASP and clarify latency/usage in frontend/src/pages/Overview.tsx.
- [x] T008 [US2] Smoke check representative overview fixtures in frontend/tests/workspace.spec.ts.

## Phase 5: US3 — Read executions
Independent check: interleaved rows become request groups; cost and rule/AI semantics are clear.
- [x] T009 [US3] Group audit rows and highlight summary cost in frontend/src/pages/AuditLog.tsx.
- [x] T010 [US3] Style boundaries and cost display in frontend/src/index.css.
- [x] T011 [US3] Smoke check request groups, risk bars and cost in frontend/tests/workspace.spec.ts.

## Phase 6: Verification and delivery
- [x] T012 Run frontend lint/build/browser checks on isolated port in frontend/playwright.config.ts and record results in specs/006-dashboard-usability/validation.md.
- [x] T013 Commit locally on feat/dashboard-usability; do not push. Reviewable description is prepared in specs/006-dashboard-usability/pr-description.md.

## Dependencies and strategy
Setup → Foundation → US1 → US2 → US3 → Verification. Deliver conversation preservation first, then overview and audit presentation. US2 Overview and US3 Audit edits could run independently in different files; this execution is sequential. Browser scenarios remain parallel through Playwright.
