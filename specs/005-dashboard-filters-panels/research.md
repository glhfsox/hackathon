# Research decisions

- **Decision**: Share an applied relative duration, keeping draft text separate. Existing endpoint contracts accept ISO `since`; no backend changes are needed. A positive whole number plus m/h/d avoids ambiguous units. Presets use `aria-pressed` and a high-contrast selected style.
- **Decision**: Invalidate old polling generations when query keys change or a page unmounts. Current `usePolled` lets delayed old responses overwrite current results. Clear old data on key changes so it is not mislabeled as the new time window.
- **Decision**: Use a small React split component with pointer capture, actual container measurements, keyboard arrows/Home/End, accessible separators, and a visible hover/focus grip. It replaces existing fixed sizes without a runtime layout library. Support cancellation and resize observation.
- **Alternatives**: A layout library, persistent workspaces, panel reordering, and a new API date-range scheme add scope without serving this request.
- **Evidence**: Read-only research agent inspected `App.tsx`, `ranges.ts`, `hooks.ts`, Overview/Audit/Playground and identified each affected call/layout. Existing `AuditFilters` already includes `since`.
