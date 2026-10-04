# Research

- Decision: keep Playground mounted under a hidden container and pass visibility for scrolling. Rationale: preserves pending async work, drafts, trace selection, and split sizes with minimal code. Alternative: lift every state field and request handler into App; more coupling.
- Decision: report usage rather than unknown budget ratios. Rationale: [HTTP API](../../contracts/http-api.md) defines zero limits as unknown, not unlimited. Alternative: deriving limits from policy cannot attribute roles from audit rows.
- Decision: remove the OWASP panel. Rationale: user explicitly requested removal after reviewing the first implementation. Alternative: retaining expanded labels no longer matches the requested UI.
- Decision: group loaded rows by request_id, keeping latest group order. Rationale: same identity used by current trace. Alternative: group by conversation_id would combine multiple decisions and not match selection.
- Decision: sum loaded turn_summary cost and tokens. Rationale: [architecture](../../docs/architecture.md) records economics on these rows; check rows must not duplicate costs. Label loaded scope because page data may be partial.
- Decision: show categorical outcomes for rules; retain score bars for Jev/fallback. Rationale: binary rule scores have no probabilistic meaning. Alternative: true/false is ambiguous for redactions and failures.
