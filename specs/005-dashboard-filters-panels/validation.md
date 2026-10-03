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
