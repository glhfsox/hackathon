# Feature Specification: PostgreSQL treasury source ingestion

**Feature Branch**: `feat/treasury-postgres`
**Created**: 2026-10-03
**Status**: Ready for planning
**Input**: Replace the generated corpus's SQLite integration with PostgreSQL. Retain raw JSONL/Markdown resources. Defer chunking, BGE-M3 embeddings, and vector search.

## User Scenarios & Testing

### User Story 1 - Load the existing source corpus (Priority: P1)

An operator loads validated financial records and document bodies into the chosen database without regenerating the fictional data.

**Why this priority**: Stored sources are the prerequisite for later retrieval work.
**Independent Test**: Load the default corpus and compare stored fields, document text, and links against the source files.

**Acceptance Scenarios**:
1. Given a valid corpus, when loaded, then every source record and document body is persisted with stable IDs and relationships.
2. Given tampered source files, when loading, then validation fails before any database write.
3. Given the corpus, when inspecting the database, then evaluation annotations, questions, and attack labels are absent.

### User Story 2 - Reliable loading and setup (Priority: P2)

An operator starts a local database, configures a connection, and repeats a load without duplicates or accidental replacement.

**Why this priority**: A reproducible demo needs explicit setup and failure behavior.
**Independent Test**: Load twice, attempt a different corpus, and verify unchanged source data and transaction rollback.

**Acceptance Scenarios**:
1. Given the same loaded corpus, when loading again, then it succeeds without duplicate rows.
2. Given a populated demo namespace and different incoming data, when loading, then it refuses replacement without changing stored rows.
3. Given a database constraint failure, when loading, then no partial data or schema changes commit.
4. Given no connection configuration, when loading, then the CLI reports the missing setting without exposing credentials.

### Edge Cases

- Polish characters and multiline malicious text survive a file-to-database round trip unchanged.
- Currency and ownership mismatch is rejected before insertion; monetary values remain integer minor units.
- An existing incompatible table causes an explicit error; the loader never drops or truncates tables.
- A failed database connection leaves source files unchanged.
- Offline generation and corpus validation remain available without a running server.

## Requirements

### Functional Requirements

- **FR-001**: Remove embedded-database snapshot generation, validation, imports, and current usage instructions.
- **FR-002**: Preserve JSONL records, Markdown bodies, metadata, evaluation artifacts, and reproducibility checks.
- **FR-003**: Add a loader that validates the full corpus before persisting source records and full text in an isolated demo namespace.
- **FR-004**: Preserve IDs, monetary precision, dates, Unicode text, and document-to-payment links.
- **FR-005**: Persist schema creation and all source inserts atomically with parameterized values and relational constraints.
- **FR-006**: Make identical repeated loads safe, reject different data in populated tables, and never silently replace existing records.
- **FR-007**: Obtain connection credentials from an environment variable; do not commit or log them.
- **FR-008**: Document a runnable local setup and exercise ingestion against a real database with automated integration tests.
- **FR-009**: Do not implement document chunks, embeddings, vectors, or search in this feature.

### Key Entities

Source and evaluation entities remain defined in [contracts/demo-data.md](../../contracts/demo-data.md). The persisted sources contain no evaluator-only records.

## Success Criteria

### Measurable Outcomes

- **SC-001**: The default load stores 12 clients, 24 contacts, 24 accounts, 1,000 transactions, and 100 complete documents.
- **SC-002**: Stored fields and document/payment links agree with the source corpus in every integration test.
- **SC-003**: Repeated loading causes zero duplicate rows; rejected changes cause zero stored source changes.
- **SC-004**: Generation and offline validation require no database server and create no embedded database file.

## Assumptions

- This is the explicitly authorized demo-client RAG extension; middleware and its audit storage are outside the migration.
- The earlier refusal to publish branches persists; implementation remains local unless the user authorizes publication.
- Local setup uses an isolated database; the loader uses a dedicated namespace to avoid touching middleware tables.
- BGE-M3 is selected for the next embedding step, not installed or executed here.
