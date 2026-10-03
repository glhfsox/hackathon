# Treasury RAG demo design

Status: raw source ingestion into PostgreSQL is implemented. Document chunking, BGE-M3 embeddings, pgvector storage/search, and agent execution are deferred.

## Domain and boundaries

Goldman Sachs's public [Transaction Banking description](https://www.goldmansachs.com/what-we-do/transaction-banking) covers payments, FX, and liquidity for corporate treasury clients. The demo uses fictional companies and original documents; it does not model proprietary Goldman Sachs systems. Other domains include investment banking, markets, and wealth management ([business overview](https://www.goldmansachs.com/what-we-do/our-businesses)).

The demo is a client of the [control layer](architecture.md). Data follows [the dataset contract](../contracts/demo-data.md). Retrieval does not define enforcement policy.

## Recommended stack

- PostgreSQL for records, document bodies, chunks, and vectors.
- [pgvector](https://github.com/pgvector/pgvector) cosine similarity. Start with exact search for a few hundred chunks; defer approximate indexes until volume warrants them.
- [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) executed directly in Python through FlagEmbedding or sentence-transformers in the next step. The multilingual model supports the Polish diacritics in the source names. Record its exact revision and dimensions, follow its input conventions, and use it for documents and queries. No Ollama embedding runtime is needed.
- Small Python ingestion/retrieval functions using a PostgreSQL client; no RAG framework initially.

One PostgreSQL database preserves records and, in the next step, vectors. PostgreSQL requires a running service; pgvector will be enabled when vector storage is implemented.

## Ingestion

1. Generate and validate the corpus. Run `load-postgres` to load structured JSONL records with stable links and complete document bodies using `documents.jsonl` and `text_path`. The loader uses a dedicated schema, validates before connecting, commits atomically, and accepts identical repeats. See [the run guide](../demo_data/README.md).
2. Preserve source/classification/client metadata. Never index `evaluation/`, questions, answers, attack/pair labels, or the manifest as document text.
3. Split by Markdown headings and paragraphs; roughly 350 tokens with 50-token overlap is an initial setting to measure. Respect model tokenizer/context limits; retain document ID, chunk index, original character range, and content hash. Do not silently truncate.
4. Prefix embedding input with title/section heading; store that input hash and model identity separately from original offsets.
5. In the next step, embed locally with BGE-M3 in batches; validate finite vectors, dimensions, response counts, and identity. Persist atomically, resume via hashes; re-embed after model/chunking changes.

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

`query_transactions` uses fixed query templates and validated arguments for exact status, amounts, dates, and totals. Aggregate separately by currency; do not use arbitrary agent-written SQL.

`search_documents` finds narrative explanations. Use the ingestion embedding model, apply caller-derived client/access filters in SQL before similarity ranking, and return top-k (start with 5) with source IDs, original text, classification, and citation offsets. Retrieved instructions remain untrusted data. Execution uses [the tool guard](../contracts/http-api.md).

Combine exact lookup and vector search for "Why was this payment held?" Vector search should not approximate numerical totals.

## Security evaluation

Normal retrieval applies caller-derived access filters. A separate test fixture can return a selected restricted or poisoned source to exercise the middleman; this must not be a caller-controlled production bypass.

Clean/poisoned supplier pairs have identical ordinary metadata and no attack flags. Avoid indexing both near-duplicates together: compare a clean corpus view with a poisoned replacement view, or inject a selected retrieved fixture to evaluate the control layer alone. Verify attacks are actually retrieved before claiming successful blocking.

Measure relevance recall@k from evaluator questions separately from PII redaction, confidentiality leakage, and injection handling under explicit policy fixtures. Record retrieved source IDs, policy version, model identity, and timings. Labels are factual, not expected decisions.

Current contracts do not define classification-based authorization or a dedicated confidentiality check. Carry classification in structured tool-result content; agree on the required policy/contract extension before claiming confidential-data enforcement. Generator labels do not implement that capability.

## Generation approach

Seeded Faker supplies localized identities; templates provide linked financial narratives and known attack spans. This is reproducible but has limited linguistic diversity. Later reviewed AI-written variants can improve realism; hold related variants out together during evaluation. Data configuration does not replace middleware policy.
