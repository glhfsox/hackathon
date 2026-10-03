# Tasks: Synthetic treasury retrieval corpus

Input: [spec](spec.md), [plan](plan.md), [research](research.md), [data model](data-model.md).

## Phase 1: Setup

- [X] T001 Establish feature scope and review checklist in specs/002-treasury-rag-data/spec.md
- [X] T002 Isolate pinned dependencies, config, and ignores in demo_data/requirements.txt, demo_data/requirements-dev.txt, demo_data/config.json, demo_data/.gitignore

## Phase 2: Foundation

- [X] T003 Define source and evaluation shapes in contracts/demo-data.md
- [X] T004 Implement strict Pydantic models and relationship/span validation in demo_data/models.py

## Phase 3: US1 - Linked financial sources

Goal: reproducible records and ordinary documents. Independent test: generation and valid SQL joins.

- [X] T005 [US1] Test determinism and relational consistency in demo_data/tests/test_corpus.py
- [X] T006 [US1] Generate seeded clients, contacts, accounts, and integer-money payments in demo_data/generate.py
- [X] T007 [US1] Export JSONL, Markdown, SQLite, and checksummed manifest in demo_data/storage.py
- [X] T008 [US1] Expose generation/validation commands with explicit failures in demo_data/__main__.py

## Phase 4: US2 - Security challenge material

Goal: exact factual annotations and paired supplier attacks. Independent test: spans/pairs and evaluation isolation.

- [X] T009 [US2] Test annotations, pairs, malformed artifacts, and minimum corpus coverage in demo_data/tests/test_corpus.py
- [X] T010 [US2] Render PII, confidential facts, clean/poisoned messages, and retrieval questions in demo_data/generate.py

## Phase 5: US3 - Retrieval design

Goal: documented future ingestion and guarded retrieval. Independent test: follow run guide and inspect artifacts.

- [X] T011 [US3] Document PostgreSQL/pgvector/Ollama ingestion and security boundaries in docs/rag-demo.md
- [X] T012 [US3] Provide runnable usage and artifact inspection in demo_data/README.md and specs/002-treasury-rag-data/quickstart.md

## Phase 6: Verification and delivery

- [X] T013 Run Ruff/pytest and generate/validate default data; record results in specs/002-treasury-rag-data/validation.md
- [ ] T014 Commit and push feature changes; prepare PR description in specs/002-treasury-rag-data/pr-description.md

## Dependencies and parallel opportunities

Setup -> foundation -> US1 -> US2 -> verification. US3 design can run independently of code after the contract. US1 export/CLI and generator share models but use different files; execute serially in this session. US2 tests can be drafted before rendering changes. MVP is US1, but all stories are required for the requested corpus.

## Implementation strategy

Validate models and deterministic links first; then add security narratives and exact annotations; finally verify the CLI output and document the future retrieval path. Do not implement embeddings or agent execution in this feature.
