# Store treasury demo sources in PostgreSQL

The retrieval demo should store transaction records and full document text in PostgreSQL. Replace the corpus's embedded snapshot integration with `load-postgres`: validate existing JSONL/Markdown sources, create an isolated schema, insert source records and normalized document/payment links, verify exact readback, and commit atomically. Identical repeat loads are accepted; a different populated corpus is rejected without replacement. Evaluation annotations and questions stay outside the database.

Setup: install the separately pinned `psycopg[binary]==3.3.2` dependency; set `DEMO_DATABASE_URL`. Optional local PostgreSQL 16 setup uses `demo_data/compose.yaml` and an ignored `.env` with `POSTGRES_PASSWORD`. No migration of middleware/audit tables occurs. Source IDs, Unicode, integer-money values, and full malicious document text are preserved. The README includes loading and SQL inspection commands.

Validation: Ruff lint passed; formatting passed (9 files). Full pytest with real PostgreSQL passed all 27 tests, including five database integration tests. Loaded the existing default corpus: 12 clients, 24 contacts, 24 accounts, 1,000 transactions, 100 documents, and 48 document/payment links. A repeated load reported `already_loaded`; inspected counts and an investigation/payment join. Raw-file validation passed after removing the obsolete generated snapshot and its manifest entry.

Scope: document chunking, BGE-M3 embeddings, pgvector storage/search, and agent execution remain the next step. RAG design now reflects the user's BGE-M3 choice. PostgreSQL source storage is implemented; confidentiality annotations still require middleware policy before implying enforcement.

This branch depends on the local generator/scaffold branch, which has not landed on main. Shared contract/design updates are kept in their own local commit. Branch publication and PR creation are deferred under the user's earlier refusal; do not merge without human instruction.
