# Test the dashboard workspace

From `frontend/`, install with `npm ci`, then run `npm test`, `npm run lint`, and `npm run build`. Browser checks use installed Google Chrome; `PLAYWRIGHT_BROWSER=chromium` selects Playwright Chromium after `npx playwright install chromium`.

Start `npm run dev` and open http://localhost:5173. The backend stays disconnected for this review. Automated browser tests supply isolated API fixtures; the ordinary dev page displays connection errors where live data would be needed.

1. On Overview choose 1h: its button is visibly selected. Enter 45m and Apply: the displayed window changes. Invalid input shows an error and preserves the window.
2. Switch to Audit: the same time selection applies. Confirm the caller/check/action/checkpoint controls, summary checkbox and export links are absent.
3. Confirm the four plain header tabs (Overview, Audit, Policy, Playground) work, with no directory sidebar, clock, NORMAL badge or status path. Drag the Audit trace divider; use arrows or Home/End when focused.
4. On Playground resize the trace column and decision/raw trace split. Drag above the prompt to enlarge it; type multiple lines and send with the button or Ctrl/Cmd+Enter.
5. Resize the browser; panes should retain reachable content and internal scrollbars. The time filter is absent on Policy and Playground.

Actual command results belong in [validation.md](validation.md).
