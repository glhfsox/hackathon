# Implementation Plan: Treasury RAG demo

**Branch**: `feat/treasury-rag` | **Date**: 2026-10-03 | **Spec**: [spec.md](spec.md)

## Summary

Extend the existing `demo_data` CLI with indexing, semantic search, and one guarded treasury agent. The authoritative design and interfaces live in [docs/rag-demo.md](../../docs/rag-demo.md) and [contracts/rag-demo.md](../../contracts/rag-demo.md).

## Technical Context

**Language/Version**: Python 3.11+ (compatible with backend 3.12).
**Primary Dependencies**: Existing Pydantic/psycopg; sentence-transformers for direct BGE-M3 dense encoding, pgvector's psycopg adapter, httpx for the middleware endpoints.
**Storage**: Existing PostgreSQL 16 volume; pinned pgvector image; raw tables unchanged, two derived index tables.
**Testing**: pytest deterministic chunk/validation/guard tests; real PostgreSQL integration; real BGE-M3 search; separate middleware-branch copy with scripted upstream.
**Target Platform**: Local macOS/Linux; CPU encoding by default, optional device configuration.
**Project Type**: CLI demo client.
**Performance Goals**: Batch embedding; unchanged indexes require no inference; no approximate index for hundreds of chunks.
**Constraints**: No framework, paid API, arbitrary SQL/shell tool, backend modifications, evaluator indexing, or invented confidentiality enforcement.
**Scale/Scope**: 100 documents, 1000 transactions, one client-scoped agent per invocation.

## Constitution Check

- I–VII: Middleware alone owns policy, checks, ordering, enforcement, and audit. Demo carries classification but adds no middleware policy logic.
- II: Announce additive demo contract and narrow existing demo design update; backend HTTP shapes stay unchanged.
- VIII: Every model turn uses the configured control-layer endpoint; every tool uses its independent guard.
- IX: Meaningful tests cover allowed/blocked guards, redaction and retrieved injection under explicit fixtures.
- X: Small CLI and exact search; no production RAG architecture.
- Scope exception is explicitly authorized by the user and recorded in AGENTS.md. Previously deferred demo work is now requested. This is the sole justified deviation from the original RAG exclusion.

## Project Structure

```text
demo_data/
  rag_models.py      # demo configuration, chunk/search/tool argument shapes
  chunking.py        # headings → paragraphs → bounded oversized paragraph
  embeddings.py      # pinned direct BGE-M3, no truncation, vector validation
  rag.py             # atomic derived indexing and fixed client-scoped queries
  rag_schema.sql    # extension + index state/chunks
  agent.py           # HTTP chat/tool guard loop
  __main__.py        # index/search/agent commands
  tests/             # deterministic and PostgreSQL integration coverage
specs/004-treasury-rag/
  spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md, validation.md
contracts/rag-demo.md
docs/rag-demo.md
```

## Implementation and Verification

First record the spec and additive contract, then install dependencies and replace the PostgreSQL image while preserving its major version and volume. Implement deterministic chunking and vector validation before indexing. Rebuild only derived data in one transaction after checking source hashes; queries verify index/encoder identity. Finish with the guarded agent and CLI. Test offline, then PostgreSQL, then actual BGE-M3 and an isolated copy of the working middleware.

No extension hooks file is present. Post-design constitution check passes with the explicit demo scope exception above.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative |
|---|---|---|
| Demo RAG was originally excluded | User explicitly requests it to test the control layer | Raw mocked strings alone do not exercise the requested retrieval pipeline |
