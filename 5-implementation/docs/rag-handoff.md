# Treasury RAG implementation and backend handoff

Updated: 2026-10-03. Run commands: [demo_data/README.md](../demo_data/README.md). Design: [rag-demo.md](rag-demo.md). Tool/client contract: [contracts/rag-demo.md](../contracts/rag-demo.md).

## Current implementation

| Part | State |
|---|---|
| Synthetic treasury records, PII/confidential documents, and poisoned supplier fixtures | Implemented; raw files retained |
| PostgreSQL source ingestion | Implemented; records and full document bodies stored |
| Structure-based Markdown chunks | Implemented; bounded paragraph fallback, original Unicode offsets, no overlap |
| BGE-M3 direct Python embeddings | Implemented; pinned model revision, 1024 dimensions, no Ollama embeddings |
| pgvector storage and exact cosine search | Implemented; same PostgreSQL 16 volume, atomic derived index |
| Client-scoped document and transaction tools | Implemented; fixed parameterized read-only queries |
| Small agent HTTP loop and independent tool guard | Implemented against existing middleware contracts; automated guard verification |
| Live agent → teammate's running backend → Ollama chat model | **Not connected or verified; intentionally deferred by the user** |
| New classification/confidentiality policy enforcement | Not implemented in this demo; classification is carried as evidence |

Actual model/database/integration verification results and limits: [validation.md](../specs/004-treasury-rag/validation.md). A scripted upstream in tests is not a live Ollama connection.

## Expected colleague work

1. Run the working control-layer backend (currently available on `origin/feature/proxy`; this feature does not merge or modify it).
2. Start the team's Ollama chat runtime and select an installed model supporting tool calls. BGE-M3 remains in this demo's Python process.
3. In the backend's existing policy, register the chat model/upstream URL and authorize a caller/key for that model and the tools `search_documents` and `query_transactions`. Use the backend's current schema; this feature adds no middleware policy fields. Ensure tool-result/output checks enforce the intended test policy.
4. Set `DEMO_CONTROL_BASE_URL` to the control-layer `/v1` URL, `DEMO_API_KEY` to that caller's key, and `DEMO_AGENT_MODEL` to the registered model name. Select the demo's client with `--client-id`; scope is trusted operator configuration, not a model argument or a new production authorization mechanism.
5. Run `python -m demo_data agent 'Why is TXN-000001 held?' --client-id CLI-0001`. Confirm both retrieval tools are usable, tool execution has a guard decision, tool results are inspected, and answers cite source IDs.
6. Run the same workflow with PII, confidential pricing, and poisoned supplier material. Check the retrieved source IDs and middleware audit before claiming protection. Do not treat classification metadata alone as a confidentiality check.

Observed test gap: with Jev explicitly disabled, the current `origin/feature/proxy` signature feed does not match DOC-00008's phrase beginning “Ignore the user's payment question”. Retrieval returns the attack, but that deterministic-only configuration allows it. A separate temporary matching signature verifies blocking at the tool-result checkpoint. Confirm live Jev detection or update the actual external feed before expecting this case to block; neither is implemented by this demo feature.

The agent never falls back to calling Ollama directly. Missing/invalid control responses stop the run. If a guard returns `redact`, its present contract supplies no replacement arguments, so this client stops rather than executing unapproved arguments.

## Deliberately small design

No recursive chunking framework, overlap tuning, sparse/hybrid retrieval, reranker, approximate vector index, arbitrary agent SQL, or shell tool is needed for these fixtures. Add only when a measured test gap requires it.
