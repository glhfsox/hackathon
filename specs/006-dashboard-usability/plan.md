# Implementation Plan: Dashboard usability

**Branch**: `feat/dashboard-usability` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

## Summary
Preserve mounted playground state during navigation. Replace unclear overview ratios with labeled measurements, use categorical rule outcomes, group audit rows by request, and highlight summary costs.

## Technical Context
- Language: existing strict TypeScript.
- Dependencies: existing React, Vite, Playwright; no additions.
- Storage: page-session React state only.
- Testing: lint, production build, browser regressions with isolated API fixtures and screenshots.
- Platform/type: browser dashboard.
- Performance goal: retain current polling cadence; no added API calls.
- Constraints: use existing single API client and contracts; backend behavior unchanged.
- Scope: App, Playground, Overview, AuditLog, shared display component and CSS.

## Constitution Check
Pre-design and post-design: PASS. I/V/VI: policy and security decisions unchanged. II: use [existing contracts](../../contracts/README.md) without copying shapes. III/IV: checks unchanged. VII: audit data untouched. VIII: integration unchanged. IX: security checks unchanged; browser regression coverage for navigation and presentation. X: bounded requested UI improvements with existing dependencies. No violations.

## Project Structure
- `frontend/src/App.tsx`: mounted hidden playground container.
- `frontend/src/pages/Playground.tsx`: active scrolling and Enter handling.
- `frontend/src/pages/Overview.tsx`: remove OWASP, show standalone response time and usage.
- `frontend/src/pages/AuditLog.tsx`: grouped row bodies and highlighted cost.
- `frontend/src/components/ui.tsx`: shared categorical rule/AI score renderer.
- `frontend/src/index.css`: targeted presentation rules.
- `frontend/tests/workspace.spec.ts`: navigation and presentation regressions.
- `frontend/playwright.config.ts`: optional isolated test port.

## Complexity Tracking
No added dependencies, durable storage, or backend changes.
