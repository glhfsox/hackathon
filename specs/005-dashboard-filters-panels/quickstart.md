# Test the dashboard workspace

From `frontend/`, install with `npm ci`, then run `npm test`, `npm run lint`, and `npm run build`. Browser checks use installed Google Chrome; `PLAYWRIGHT_BROWSER=chromium` selects Playwright Chromium after `npx playwright install chromium`.

Start `npm run dev` and open http://localhost:5173. Run the backend on port 8000 to inspect real data; configure the Playground caller as documented in `frontend/AGENTS.md`.

1. On Overview choose 1h: its button is visibly selected. Enter 45m and Apply: the displayed window changes. Invalid input shows an error and preserves the window.
2. Switch to Audit: the same selection applies. Add caller/action filters and inspect CSV/JSON links for the same time window and local filters.
3. Drag the sidebar edge and the Audit trace divider. Focus each handle and use arrows or Home/End.
4. On Playground resize the trace column and decision/raw trace split. Drag above the prompt to enlarge it; type multiple lines and send with the button or Ctrl/Cmd+Enter.
5. Resize the browser; panes should retain reachable content and internal scrollbars. The time filter is absent on Policy and Playground.

Actual command results belong in [validation.md](validation.md).
