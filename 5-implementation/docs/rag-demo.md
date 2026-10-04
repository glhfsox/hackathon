# Treasury RAG demo design

Status: PostgreSQL source ingestion, structure-based chunking, direct BGE-M3 embeddings, pgvector search, and a guarded agent client are implemented. Live connection to a teammate's backend/chat model is intentionally pending; see [handoff](rag-handoff.md).

## Domain and boundaries

Goldman Sachs's public [Transaction Banking description](https://www.goldmansachs.com/what-we-do/transaction-banking) covers payments, FX, and liquidity for corporate treasury clients. The demo uses fictional companies and original documents; it does not model proprietary Goldman Sachs systems. Other domains include investment banking, markets, and wealth management ([business overview](https://www.goldmansachs.com/what-we-do/our-businesses)).

The demo is a client of the [control layer](architecture.md). Data follows [the dataset contract](../contracts/demo-data.md). Retrieval does not define enforcement policy.

## Recommended stack

- PostgreSQL for records, document bodies, chunks, and vectors.
- [pgvector](https://github.com/pgvector/pgvector) cosine similarity. Start with exact search for a few hundred chunks; defer approximate indexes until volume warrants them.
- [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) executed directly in Python through sentence-transformers. The multilingual model supports the Polish diacritics in the source names. The exact revision, 1024 dimensions, CLS pooling, and normalized encoding are shared by documents and queries. No Ollama embedding runtime is needed.
- Small Python ingestion/retrieval functions using a PostgreSQL client; no RAG framework initially.

One PostgreSQL database preserves records and vectors. The pgvector-enabled PostgreSQL 16 image retains the existing named volume. Indexing enables the extension explicitly; a populated volume does not rerun initialization scripts.

## Ingestion

1. Generate and validate the corpus. Run `load-postgres` to load structured JSONL records with stable links and complete document bodies using `documents.jsonl` and `text_path`. The loader uses a dedicated schema, validates before connecting, commits atomically, and accepts identical repeats. See [the run guide](../demo_data/README.md).
2. Preserve source/classification/client metadata. Never index `evaluation/`, questions, answers, attack/pair labels, or the manifest as document text.
3. Split by Markdown headings, tracking heading context and ignoring heading-like lines inside fenced code. Keep short sections whole; oversized sections split at paragraph boundaries. Only a single oversized paragraph needs a tokenizer-checked bounded character fallback. Start without overlap; this avoids duplicating attacks across results. The default 350-token budget includes title/heading context and special tokens. Retain document ID, chunk index, exact original character range, and content hash. Do not silently truncate.
4. Prefix embedding input with title/section heading; store that input hash and model identity separately from original offsets.
5. Embed locally with BGE-M3 in batches; validate finite nonzero vectors, dimensions, response counts, and identity. Persist the complete derived index atomically. An identical repeated index requires no model inference; changed source/model/chunk settings require explicit `--rebuild`, which only replaces derived data. A failed rebuild preserves the previous index. Search refuses stale/mismatched indexes.

A general recursive chunker is useful for future long, irregular sources but adds little for these structured fixtures. The small fallback above covers the current oversized-section case. Parameters live in `demo_data/rag-config.json`; shapes and CLI live in [the RAG contract](../contracts/rag-demo.md).

Generator preserves whole documents so later chunk strategies can be tested, including attacks crossing boundaries.

## Retrieval tools

```text
User -> agent through /v1/chat/completions
     -> guarded tool call
        -> query_transactions: parameterized bounded SQL
        -> search_documents: local query embedding -> pgvector top-k
     -> tool results sent through control-layer tool_result checkpoint
     -> agent continuation through output/tool_call checkpoint
```

`query_transactions` uses fixed query templates and validated payment/status/limit arguments for exact records and totals. Totals cover the full matching set, even when displayed rows are limited; credit/debit totals stay separate by currency. Do not use arbitrary agent-written SQL.

`search_documents` finds narrative explanations. Use the ingestion embedding model, filter to the operator-configured client plus global public documents in SQL before ranking, and return top-k (default 5) with source IDs, original text, classification, and citation offsets. Optional payment filtering uses document/payment links. The model cannot change client scope; binding authenticated callers to clients remains a future backend integration concern. Retrieved instructions remain untrusted data. Execution uses [the tool guard](../contracts/http-api.md).

Combine exact lookup and vector search for "Why was this payment held?" Vector search should not approximate numerical totals.

## Security evaluation

Normal demo retrieval applies the configured client's scope. All classifications for that client are available to test the middleman, including restricted PII and confidential business facts. This is explicit demo configuration, not a production authorization bypass or confidentiality policy.

Clean/poisoned supplier pairs have identical ordinary metadata and no attack flags. The current index retains both so fixtures remain faithful. Always inspect retrieved IDs/text before claiming an injection was blocked; a benign counterpart can outrank a poisoned section. Separate clean/poisoned corpus views would be needed for a controlled relevance comparison, and are not implemented here.

Measure relevance recall@k from evaluator questions separately from PII redaction, confidentiality leakage, and injection handling under explicit policy fixtures. Record retrieved source IDs, policy version, model identity, and timings. Labels are factual, not expected decisions.

Current contracts do not define classification-based authorization or a dedicated confidentiality check. Carry classification in structured tool-result content; agree on the required policy/contract extension before claiming confidential-data enforcement. Generator labels do not implement that capability.

## Generation approach

Seeded Faker supplies localized identities; templates provide linked financial narratives and known attack spans. This is reproducible but has limited linguistic diversity. Later reviewed AI-written variants can improve realism; hold related variants out together during evaluation. Data configuration does not replace middleware policy.
