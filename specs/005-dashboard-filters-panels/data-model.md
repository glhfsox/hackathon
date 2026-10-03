# UI state

- Applied window: positive whole minutes and a readable duration label; invalid drafts do not mutate it. Presets and custom input produce the same model.
- Audit filters: existing [HTTP contract](../../contracts/http-api.md), combined with the applied window at request/export time.
- Split size: first panel's percentage, default ratio, minimum first/second pixel sizes, measured container size. Clamp when dragging or the container changes.
- Poll generation: increments per request and on effect cleanup; only the current generation commits data/error/loading.

No shared wire model changes.
