# Implementation Plan: PostgreSQL treasury source ingestion

**Branch**: `feat/treasury-postgres` | **Date**: 2026-10-03 | **Spec**: [spec.md](spec.md)

## Summary

Remove the corpus SQLite snapshot and add PostgreSQL ingestion of validated JSONL/Markdown resources. Source shapes and database mapping live in [the contract](../../contracts/demo-data.md); design lives in [docs/rag-demo.md](../../docs/rag-demo.md).

## Technical Context

**Language/Version**: Existing Python 3.11+ demo package.
**Primary Dependencies**: Add pinned `psycopg[binary]==3.3.2`; it provides safe parameter binding and transaction management without a new ORM. Existing Faker/Pydantic unchanged.
**Storage**: PostgreSQL 16, dedicated `demo_data` schema; source exports remain portable files.
**Testing**: Existing pytest/Ruff plus actual PostgreSQL integration tests, explicitly skipped only if the test connection environment variable is absent.
**Target Platform**: Local macOS/Linux with optional Docker Compose PostgreSQL service.
**Project Type**: Internal CLI and SQL schema.
**Performance Goals**: Load 1,000 transactions and 100 documents in one local transaction.
**Constraints**: No paid APIs, no evaluation-label ingestion, no destructive reloads, no embeddings/vector extension yet.
**Scale/Scope**: Existing default corpus and configurable generator counts. Preserve current raw source files; remove obsolete generated snapshots and manifest entries.

## Constitution Check

- I: No middleware policy behavior introduced.
- II: Shared contract updated before code; specs link to authoritative shapes/design.
- III–VIII: Middleware checks, Jev/Ollama fallback, audit, and HTTP interfaces unchanged.
- IX: Deterministic validation and real database persistence receive tests.
- X: User explicitly authorized demo-client ingestion; retrieval is deferred.
- Post-design review: pass with the already-authorized RAG demo exception.

## Project Structure

```text
demo_data/storage.py             # portable exports and offline validation
demo_data/postgres.py            # transactional source ingestion/readback
demo_data/schema.sql             # namespace, tables, constraints, indexes
demo_data/__main__.py            # generate, validate, load-postgres
demo_data/compose.yaml           # local database service
demo_data/.env.example           # variable names only
demo_data/tests/test_postgres.py # actual database integration
specs/003-treasury-postgres/     # spec, plan, research, tasks, run guide
```

## Complexity Tracking

The user-authorized demo RAG exception remains limited to sources. This feature depends on the local generator branch because main has not received the scaffold or generator yet. Publication is deferred under the user's earlier refusal.
