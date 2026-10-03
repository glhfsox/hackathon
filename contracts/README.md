# contracts/

The one source of truth for data shapes shared between the backend, frontend, demo agent and tests. Code mirrors these files: Pydantic models in the backend, TypeScript types in `frontend/src/api/`. How the parts fit together is described in [`docs/architecture.md`](../docs/architecture.md).

| File | Contains |
|------|----------|
| [`models.md`](models.md) | CanonicalRequest, CheckResult, Decision, JudgeInput/JudgeVerdict, AuditRecord |
| [`http-api.md`](http-api.md) | Every HTTP endpoint, request and response |
| `policy.example.yaml` | **OPEN:** policy schema by example. Not written yet |
| [`demo-data.md`](demo-data.md) | Synthetic financial corpus and evaluator-only annotations for the RAG demo |
| [`rag-demo.md`](rag-demo.md) | Demo indexing, retrieval tools, configuration, and guarded agent behavior |

Rules:
- A change to a contract is its own small PR, announced to the team, and it updates the code on both sides in the same PR or immediately after.
- Field names are `snake_case` everywhere, including JSON on the wire.
- Additive changes (a new optional field) are safe. Renames and removals are breaking and need the whole team's agreement.

Status: **draft v0.1**. Items marked **OPEN** are undecided.
