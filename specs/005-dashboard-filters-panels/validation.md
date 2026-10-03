# Verification record

Date: 2026-10-03. Branch: `feat/dashboard-filters-panels`, based on dev at `9721aca`.

## Commands actually run

- `npm test -- --workers=2 --timeout=5000` before implementation: all five initial browser cases failed against the original UI, including the delayed-response regression.
- `npm test -- --workers=2` after implementation: **6 passed in 10.2s**, running installed Google Chrome. Test-only API fixtures isolate browser behavior; they are not shipped application data.
- `npm run lint`: exit 0 with no lint diagnostics.
- `npm run build`: exit 0; TypeScript and Vite produced the production assets (27 modules).
- `./node_modules/.bin/tsc --ignoreConfig --noEmit --module nodenext --target es2023 --types node --strict --skipLibCheck playwright.config.ts tests/workspace.spec.ts`: exit 0. The preliminary command omitted Node types; the corrected explicit invocation passes.
- `git diff --check`: exit 0.
- `npm test -- --grep 'all panel dividers' --repeat-each=3 --workers=1`: **3 passed in 11.2s**, confirming the settled-layout drag checks repeat successfully.

## Browser coverage

All four preset durations generate the expected metrics query and visibly change selected state. Custom 45m carries to Audit, back to Overview, and both exports while retaining the caller filter. Invalid/empty/zero/negative/partial/fractional/overflowing values preserve the active window. Delayed responses for an old window cannot overwrite the newly selected metrics.

Every named divider changes by pointer and keyboard and reaches bounded Home/End limits; double-click resets its default. Prompt resizing preserves a multiline draft. A 900×650 viewport keeps the prompt and Send visible without horizontal document overflow. Shortcut and button submission preserve the allowed conversation history and show the returned decision trace.

Screenshots were captured and visually reviewed. An initial drag test raced a preceding panel reset; the final test waits for the handle to settle and for the actual accessible size change.

## Limits and manual access

The existing Vite server on http://localhost:5173 serves this checkout with hot reload. Backend startup for a live UI/API smoke was declined, so that integration was **not run**. Browser fixtures prove frontend request construction/interactions; they do not prove live Jev/Ollama responses. Existing backend/model/caller configuration is still required for live Playground chat.

Only a development dependency was added: Playwright. No runtime dependency, backend API, production policy, or security decision changed. Panel sizes are retained while their components are mounted; reload persistence and panel reordering are outside scope.

User confirmed that they will later merge this feature into dev. No feature merge into main or dev was performed.

## Shell simplification follow-up (2026-10-03)

- `npm run lint` and `npm run build` from frontend: both exit 0; Vite built 27 modules. An initial invocation from the repository root failed because the package is inside frontend; corrected by running in the package directory.
- `npm test -- --workers=2`: **6 passed in 9.2s**, using Chrome and test-only API route fixtures. Initial sandbox attempt could not bind the local frontend port (EPERM); the approved run succeeded. No backend was started or contacted by these tests.
- Screenshots reviewed at 1440×900 and 900×650: larger four-tab header, no directory sidebar/clock/status path, full-width panels, useful Playground guidance, and accessible prompt controls with no horizontal document overflow.
- Existing resize coverage now verifies the removed navigation divider stays absent and the remaining Audit/Playground handles still work; header tab count and active-page semantics are checked.
- `git diff --check`: exit 0.

Changes remain on the local feature branch; no push or merge is requested for this follow-up.

## Plain headings follow-up

The user clarified that time-window presets and custom durations must stay. All time and Audit field filtering is preserved; only the ornamental Filters heading/frame, terminal-style pane titles and decorative square brackets were removed.

- `npm test -- --workers=2`: **6 passed in 14.3s**, with isolated test-only backend responses. Existing filter/export/validation/race/resize/prompt coverage still passes; the resize case also checks plain tab labels and the Audit heading.
- `npm run lint`: exit 0 with no diagnostics after removing a redundant regex escape in the test assertion.
- `npm run build`: exit 0; TypeScript/Vite built 27 modules. An intermediate build caught a stale reference while exploring filter removal, before the clarification was incorporated; the final implementation restores the filter completely.
- `git diff --check`: exit 0.
- Overview and 900×650 Playground screenshots were visually reviewed: ordinary headings, no decorative tab brackets, and reachable prompt controls.

No backend startup, push or merge was performed.

## Audit toolbar removal (2026-10-04)

- Removed caller/check/action/checkpoint controls, summary toggle and export links; Audit still queries the selected shared time window and retains its table/trace split. Removed numbered prefixes from the four navigation tabs.
- `npm test -- --workers=2`: **6 passed in 16.9s**, with test-only API fixtures and no backend startup. Updated browser coverage checks the missing toolbar controls and plain tab labels while verifying the shared time-window request.
- `npm run lint` and `npm run build`: exit 0 with no lint diagnostics; TypeScript/Vite built 27 modules.
- `git diff --check`: exit 0.

All changes remain local on feat/dashboard-filters-panels; no push or merge.
