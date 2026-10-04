# Validation

From `frontend/` run `npm run lint`, `npm run build`, and `PLAYWRIGHT_PORT=5174 CI=1 npm test` (installed Chrome).

Run `npm run dev -- --host 127.0.0.1 --port 5174 --strictPort` to review separately from Docker on 5173. Use the normal port 5173 for a live backend session because backend CORS allows that origin.

- Send with Enter; Shift+Enter adds a line. Switch tabs mid-request and after a reply; drafts, history and selected trace persist.
- Overview omits the OWASP panel and shows, milliseconds with explained percentiles, and daily tokens/cost without infinity.
- Audit separates requests, shows rule outcomes and AI bars, and highlights selected request cost.
- Browser fixtures verify blocked history stays visible but is excluded from follow-up requests.
