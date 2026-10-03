# Tasks: Treasury RAG demo

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md).
**Tests**: Required by FR-011 and repository rules for deterministic logic.

## Phase 1 — Setup

- [x] T001 Record the feature specification and quality checklist in specs/004-treasury-rag/spec.md and checklists/requirements.md.
- [x] T002 Complete researched design and additive interface contract in specs/004-treasury-rag/plan.md and contracts/rag-demo.md.
- [x] T003 Pin optional RAG dependencies in demo_data/requirements-rag.txt and add the pgvector image to demo_data/compose.yaml, retaining the PostgreSQL 16 volume.

## Phase 2 — Foundation

- [x] T004 Define validated config/chunk/search/tool models in demo_data/rag_models.py; token budget 64..2048, dimensions 1024, top-k 1..10, transaction limit 1..100, no extra tool fields.
- [x] T005 Add canonical defaults to demo_data/rag-config.json and name-only env entries to demo_data/.env.example.

## Phase 3 — US1: Index and search (P1)

**Independent verification**: exact source coverage/offsets, bounded embedding inputs, real vector ranking and client/payment filters, unchanged repeat with no inference.

- [x] T006 [US1] Write deterministic chunk/vector checks in demo_data/tests/test_chunking.py and test_embeddings.py before implementation.
- [x] T007 [US1] Implement fenced-heading and paragraph chunking with bounded fallback in demo_data/chunking.py, preserving every original character without overlap.
- [x] T008 [US1] Implement pinned direct BGE-M3 encoding and finite/nonzero 1024-vector validation in demo_data/embeddings.py.
- [x] T009 [US1] Write PostgreSQL index/search/rollback/identity checks in demo_data/tests/test_rag.py.
- [x] T010 [US1] Implement derived vector schema in demo_data/rag_schema.sql and atomic indexing/exact scoped retrieval in demo_data/rag.py.
- [x] T011 [US1] Expose index/search commands with safe explicit errors in demo_data/__main__.py.

## Phase 4 — US2: Guarded agent (P1)

**Independent verification**: full chat history, canonical tool guards, blocked/malformed/unavailable guard executes no tool, exact per-currency transaction totals.

- [x] T012 [US2] Write agent boundary tests in demo_data/tests/test_agent.py before implementation.
- [x] T013 [US2] Implement validated HTTP completion/guard loop in demo_data/agent.py, including guard redaction refusal and step-limit status.
- [x] T014 [US2] Complete fixed scoped transaction tool in demo_data/rag.py and expose configurable agent command in demo_data/__main__.py.

## Phase 5 — US3: Real security inputs (P2)

**Independent verification**: actual BGE retrieval of PII and poisoned sources followed by real middleware redaction/blocking under explicit temporary policy.

- [x] T015 [US3] Add opt-in real middleware verification with scripted upstream in demo_data/tests/test_middleware.py, without importing backend in ordinary tests.
- [x] T016 [US3] Run real BGE-M3/pgvector and isolated middleware verification; record exact evidence in specs/004-treasury-rag/validation.md.

## Phase 6 — Delivery

- [x] T017 Update canonical run instructions/design in demo_data/README.md and docs/rag-demo.md; append the authorized decision in AGENTS.md.
- [x] T018 Run appropriate pytest/Ruff checks and document outputs/limits in specs/004-treasury-rag/validation.md and pr-description.md.

## Dependencies and implementation strategy

Setup → foundation → US1 → US2 → US3 → delivery. US2 boundary tests can run with a fake tool runner independently of a model/database. US1 is the first usable increment. Every task names an exact file; mark completion only after verification.

Parallel opportunities: independent research was delegated as required by speckit-plan. Chunk/embedding tests and agent boundary tests can be developed independently once contracts/models exist. Implementation here remains sequential to avoid file conflicts; no additional agent delegation is needed.

No extension hooks file is present. All 18 tasks use checklist IDs and story labels where applicable.
