# feat: add treasury RAG retrieval and guarded demo agent

The treasury fixtures were stored in PostgreSQL but could not be retrieved by a demo agent. This change indexes full document bodies into structure-based chunks, embeds them with pinned BGE-M3, and searches pgvector in the same database. A small agent client uses the existing control-layer completion and tool-guard endpoints, with two scoped read-only tools for document search and exact transactions.

Source text, PII, confidential material, and poisoned instructions remain intact for middleware tests. Evaluation labels stay outside the database. Unchanged indexing reuses embeddings; changed source/settings require an explicit atomic rebuild. Oversized sections split at paragraphs with a bounded tokenizer fallback; no recursive framework or approximate vector index is added.

## Teammate setup and pending work

- Install `demo_data/requirements-rag.txt`. First use downloads about 2.3 GB of BGE-M3 weights; set `HF_HOME` to an ignored local cache if desired.
- Start the updated Compose service. It retains the PostgreSQL 16 named volume and uses pgvector 0.8.7 on trixie, matching the existing volume's Debian generation. Run `python -m demo_data index`; it creates only the derived chunk/index tables and vector extension.
- Continue using `DEMO_DATABASE_URL`. Live agent runs additionally need `DEMO_CONTROL_BASE_URL`, `DEMO_API_KEY`, and `DEMO_AGENT_MODEL`, with the model and both tool names authorized by the backend policy.
- **Live backend/Ollama connection remains unimplemented and unverified by user request.** Follow [docs/rag-handoff.md](../../docs/rag-handoff.md) to finish it. The backend branch is not merged or changed by this feature.

## Verification

- Real BGE-M3 indexed 100 documents into 424 chunks. Full-corpus SQL checks found 1024 dimensions throughout, zero text mismatches, and zero coverage errors.
- Full demo suite with PostgreSQL and an isolated copy of the real middleware: **64 passed**, one Starlette/httpx deprecation warning.
- Offline suite: **50 passed, 14 explicitly skipped**. Ruff lint/format passed.
- Real retrieved PII was redacted. A temporary matching injection signature blocked poisoned tool content before the next model turn. The current default feed misses that phrase with Jev disabled; this gap is documented rather than claimed as protection.
- Confidentiality is carried as source classification; no new enforcement policy or live Jev verification is included.

Exact commands, outputs, and limitations: [validation.md](validation.md). Operator client scope is demo configuration, not a new production authorization service.
