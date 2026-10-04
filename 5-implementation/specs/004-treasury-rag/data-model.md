# Data model

Authoritative fields and constraints: [demo RAG contract](../../contracts/rag-demo.md). Raw tables remain governed by [dataset contract](../../contracts/demo-data.md).

- `document_chunks`: source range and section context; one vector per chunk; document foreign key; source/input hashes; index position unique within document.
- `rag_index`: singleton identity for the derived corpus. Model and chunk settings plus sorted document fingerprints identify the run; source and chunk counts support consistency checks.
- Search result: chunk/source IDs, exact text and original offsets, title, classification, client, similarity, and section path.
- Transaction response: bounded records plus integer credit/debit totals grouped by currency across the full matching set.
- Agent run: status (`answered`, `blocked`, `error`, `limit`), answer/reason, turn count, observed decisions, retrieved source IDs.

Index transitions: absent → indexed; identical repeat → reuse; changed identity → refusal unless `--rebuild`; successful rebuild atomically replaces derived rows; failure rolls back. Raw sources are never deleted or updated.
