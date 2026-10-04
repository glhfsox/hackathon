# Implementation Plan: Synthetic treasury retrieval corpus

**Branch**: `feat/treasury-rag-data` | **Date**: 2026-10-03 | **Spec**: [spec.md](spec.md)

**Superseded storage design**: Source generation remains in use; the embedded snapshot and Ollama embedding proposal were replaced by [the PostgreSQL migration](../003-treasury-postgres/plan.md) and the current [RAG design](../../docs/rag-demo.md). This plan records the original implementation.

## Summary

Build an isolated Python CLI under `demo_data/` that produces linked financial records, documents, evaluator annotations, retrieval questions, and a SQLite snapshot. Retrieval design lives in [docs/rag-demo.md](../../docs/rag-demo.md). Data shapes live in [contracts/demo-data.md](../../contracts/demo-data.md).

## Technical Context

**Language/Version**: Python 3.11+; available 3.11.9; compatible with backend Python 3.12.
**Primary Dependencies**: Faker 40.39.0 for localized values; Pydantic 2 for validation. Exact versions recorded in requirements files.
**Storage**: JSONL, Markdown, SQLite snapshot. Future retrieval: PostgreSQL + pgvector, local Ollama embeddings.
**Testing**: pytest; Ruff lint/format; CLI generation, validation, and SQL inspection.
**Target Platform**: macOS/Linux local demo.
**Project Type**: Internal data-generation CLI.
**Performance Goals**: Default generation without a model or server.
**Constraints**: Synthetic only; no paid APIs; no evaluation-label leakage; no overwrites.
**Scale/Scope**: Default 12 clients, 1,000 transactions, 100 documents; configurable counts. Retrieval execution deferred by user selection.

## Constitution Check

- I: Labels facts rather than enforcing hard-coded middleware decisions.
- II: Data interface in contracts; RAG design in docs; specs link to both.
- III–VIII: Checks, ordering, Jev, audit, and HTTP interfaces unchanged.
- IX: Deterministic generation and validation receive automated tests.
- X: Generator/design only; explicit user request authorizes RAG demo scope exception.
- Post-design check: pass with scope exception recorded below.

## Project Structure

```text
demo_data/
  __main__.py, models.py, generate.py, storage.py
  config.json, requirements.txt, requirements-dev.txt, pyproject.toml
  tests/test_corpus.py, README.md, .gitignore
contracts/demo-data.md
docs/rag-demo.md
specs/002-treasury-rag-data/
  spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md
```

**Structure Decision**: Isolate generation from teammates' active backend/frontend branches. Generated bulk output remains local and reproducible.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Earlier RAG exclusion | User explicitly requested RAG data and design | Detector-only strings cannot exercise malicious retrieved documents |

The branch includes the existing origin/dev scaffold because main lacks docs/contracts/spec-kit. A PR to main depends on that scaffold landing first.
