# Data model

No wire or persisted model changes. Use [shared models](../../contracts/models.md) and [HTTP API](../../contracts/http-api.md).

Playground retains its existing turns, draft, busy flag, selected index, and safe-history derivation throughout tab navigation. Hidden → visible triggers scrolling; requests continue while hidden. Blocked and failed turns remain excluded from forwarded history.

Audit groups loaded records by request_id. Records without request_id are separate system events. Only turn_summary rows contribute to cost and token totals. Rule display uses action; Jev/fallback display uses score. Nothing is inferred as a new security decision.
