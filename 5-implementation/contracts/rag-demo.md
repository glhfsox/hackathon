# Treasury RAG demo contract

Additive CLI/tool contract; middleware endpoints remain [http-api.md](http-api.md). Source shapes remain [demo-data.md](demo-data.md).

## Configuration and CLI

`rag-config.json` configures `model_name` (`BAAI/bge-m3`), immutable 40-character `model_revision`, `dimensions` (1024), `max_chunk_tokens` (64..2048; default 350, includes special tokens/title/section), `batch_size` (1..64; default 8), and `device` (default cpu). Index stores model/chunker identity, including algorithm version; batch/device do not change logical identity.

- `index [--config PATH] [--schema demo_data] [--rebuild]`: read stored documents, create extension/index tables, persist complete vectors atomically. Unchanged repeated runs reuse embeddings; changed source/settings require `--rebuild`.
- `search QUERY --client-id ID [--transaction-id ID] [--k 5] [--config PATH] [--schema demo_data]`: return ranked JSON citations.
- `agent QUESTION --client-id ID [--model NAME] [--max-steps 6] [--config PATH] [--schema demo_data]`: guarded tool-calling client.

Connection from `DEMO_DATABASE_URL`. Agent uses `DEMO_CONTROL_BASE_URL` (default `http://127.0.0.1:8000/v1`), `DEMO_API_KEY`, and `DEMO_AGENT_MODEL` (or `--model`). First RAG use downloads pinned model weights; offline generator/validator need no model. `HF_HOME` may point to a local model cache.

## Chunk/index persistence

`document_chunks`: `chunk_id`, `document_id`, `chunk_index`, `section_path` (text array), `char_start`, `char_end`, `text`, `embedding_input`, `token_count`, `source_sha256`, `input_sha256`, `embedding` (`vector(1024)`). Offsets are Unicode codepoints and `text == document.text[char_start:char_end]`. Ranges cover each nonempty document exactly, without overlap. Heading paths are context, not evaluator labels. No PII/attack cleaning occurs.

`rag_index`: singleton id 1, `identity` JSON (model/revision/dimensions/chunk budget/algorithm), `source_sha256`, `documents`, `chunks`, `indexed_at`. No evaluator files are loaded. Identity mismatch/stale source causes a clear error before serving search results.

Search filters before ranking: the operator-configured client's sources plus global documents classified public. Optional transaction ID requires a matching source link. The model cannot supply client scope. All classifications within the configured client remain eligible for middleware tests; this is not a production authentication/authorization contract.

## Tools

`search_documents`: `{query: nonempty string (max 8000 chars), k: integer 1..10 = 5, transaction_id: string | null}`. Returns `chunks`: each contains chunk/document ID, client ID, title, classification, section path, original text, character offsets, and cosine similarity. Invalid/unknown clients fail explicitly; client ID is bound by the operator outside tool arguments.

`query_transactions`: `{transaction_id: string | null, status: settled|pending|held|null, limit: integer 1..100 = 20}`. Returns exact source `transactions` (bounded), `matching_count`, and `totals_by_currency` with integer credit/debit amounts across the full match. No arbitrary SQL. Filters always include configured client ID.

Tool arguments reject unknown fields, type coercion, and unbounded requests. Results contain source classification and citations, but never evaluation labels. Query embeddings share the stored index model identity.

## Agent boundary behavior

Chat requests use standard OpenAI wire messages/tool definitions and resend complete history. Incoming completions must have valid `control` decisions. Guard calls convert history and calls to the canonical shapes in [models.md](models.md); they include the same API key. Execution requires `allowed:true` plus an `allow` or `flag` tool-call decision. A `redact` decision has no approved replacement arguments in the current guard contract, so execution stops.

Internally search returns structured hit dictionaries. In the agent's tool-message content, each hit has one `Source: {metadata JSON}` line followed by the verbatim document text with real line breaks. This preserves the original content inspected by the middleware; JSON-escaping entire bodies would turn newlines into literal `\\n` sequences. Transaction tool content stays JSON.

Blocked completions never execute tools. Guard/HTTP/protocol errors fail closed, with no direct upstream fallback. Invalid local tool arguments are returned as a bounded error tool message; they never reach the database. Unknown tools are not executed. Guard denials terminate the run. Step limit is reported explicitly.

Run JSON: `status` (`answered|blocked|error|limit`), `answer`, `steps`, `decisions` (proxy and guard), `source_ids`. Only the final model answer is shown as answer; raw tool bodies are not printed as a transcript.

Classification is evidence for the middleware; this feature does not add confidential-data enforcement or claim it exists.
