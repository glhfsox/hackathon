# Tasks: PostgreSQL treasury source ingestion

Input: [spec](spec.md), [plan](plan.md), [research](research.md), [data model](data-model.md).

## Phase 1: Setup

- [X] T001 Record migration scope and review checklist in specs/003-treasury-postgres/spec.md
- [X] T002 Pin the PostgreSQL driver and add local service/credential examples in demo_data/requirements.txt, demo_data/compose.yaml, demo_data/.env.example

## Phase 2: Foundation

- [X] T003 Update source artifacts and persistence contract in contracts/demo-data.md
- [X] T004 Remove SQLite snapshot creation/validation in demo_data/storage.py and update demo_data/tests/test_corpus.py

## Phase 3: US1 - Persist source records

Independent test: database readback matches source fields and exact bodies.

- [X] T005 [US1] Define PostgreSQL source tables, BIGINT money, timestamps, and links in demo_data/schema.sql
- [X] T006 [US1] Implement validated transactional inserts and readback in demo_data/postgres.py
- [X] T007 [US1] Expose load-postgres using DEMO_DATABASE_URL in demo_data/__main__.py
- [X] T008 [US1] Verify actual PostgreSQL rows, Unicode/text fidelity, and evaluation isolation in demo_data/tests/test_postgres.py

## Phase 4: US2 - Repeat and failure handling

Independent test: identical repeated load, conflict rejection, and database failure rollback.

- [X] T009 [US2] Test repeated loads, mismatch refusal, invalid-source preflight, and rollback in demo_data/tests/test_postgres.py
- [X] T010 [US2] Document setup and accepted BGE-M3 next step in demo_data/README.md and docs/rag-demo.md

## Phase 5: Verification

- [X] T011 Remove obsolete generated SQLite snapshots and update their manifest inventories under demo_data/generated/
- [X] T012 Run unit/integration/lint/format and default corpus loading; record actual results in specs/003-treasury-postgres/validation.md
- [X] T013 Commit dependency pins separately and prepare local delivery notes in specs/003-treasury-postgres/pr-description.md

## Dependencies and implementation strategy

Setup -> foundation -> US1 -> US2 -> verification. Models and source generation are unchanged. Independent documentation can be drafted alongside tests; execution stays serial in this session. US1 is the minimum migration; both stories are required before completion. Publication remains deferred by the user's earlier refusal.
