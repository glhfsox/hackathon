# Feature Specification: Synthetic treasury retrieval corpus

**Feature Branch**: `feat/treasury-rag-data`
**Created**: 2026-10-03
**Status**: Ready for implementation
**Input**: Generate financial transaction records and text documents for a RAG demo that exercises the AI Control Layer. Implement data creation now and design retrieval for later.

## User Scenarios & Testing

### User Story 1 - Generate a linked financial corpus (Priority: P1)

An operator creates fictional corporate treasury clients, contacts, accounts, payments, and related documents with one command.

**Why this priority**: The demo needs retrievable source material rather than standalone detector strings.
**Independent Test**: Generate a corpus, open its database, and trace every account, payment, and client-specific document to an existing client.

**Acceptance Scenarios**:
1. Given the same seed and configuration, when generation runs twice, then the exported records, documents, and answer keys are identical.
2. Given a generated corpus, when an operator follows document payment references, then the amounts, currency, status, and client agree with the structured records.
3. Given an existing output directory, when generation runs, then it refuses to overwrite its contents.

### User Story 2 - Retrieve realistic security challenges (Priority: P2)

An evaluator has ordinary documents, personal identifiers, confidential business facts, and poisoned supplier messages with separate ground truth.

**Why this priority**: Security evaluation requires realistic retrieved context and benign comparisons.
**Independent Test**: Verify annotated text spans and clean/poisoned document pairs against the source bodies.

**Acceptance Scenarios**:
1. Given a contact document, when its annotations are checked, then each annotated personal identifier matches the exact source span.
2. Given a supplier message pair, when the bodies are compared, then the clean document contains ordinary payment information and the poisoned variant adds an attack with a recorded family and source span.
3. Given the corpus, when a retriever consumes its source files, then evaluator-only attack labels and answers are not part of document bodies or retrieval metadata.

### User Story 3 - Prepare the retrieval implementation (Priority: P3)

A developer can follow a documented design for structured queries, document chunking, local embedding, vector retrieval, and control-layer integration.

**Why this priority**: The generator must produce material the later retrieval pipeline can ingest.
**Independent Test**: Review the run guide and design against existing control-layer contracts.

**Acceptance Scenarios**:
1. Given a generated corpus, when a developer reads the design, then the paths from source records and documents to agent tool results are explicit.
2. Given confidentiality annotations, when reviewing expected security behavior, then factual labels are distinct from policy-dependent allow/redact/block decisions.

### Edge Cases

- Invalid counts, seed types, unknown config fields, and unsupported locales are rejected before generation.
- Output collisions are rejected without replacing existing files.
- Multiple identifiers in one document and multiline attacks retain exact character offsets.
- Small corpora still include benign, PII, confidential, and attack examples.
- Foreign-key references and money values remain valid at larger configured sizes.

## Requirements

### Functional Requirements

- **FR-001**: Generate only fictional financial records and original documents; no private Goldman Sachs material is required.
- **FR-002**: Provide configurable counts and seed, stable identifiers, fixed reference dates, and generation provenance.
- **FR-003**: Maintain relational links between clients, contacts, accounts, transactions, and documents; monetary values use integer minor units with a currency.
- **FR-004**: Include public operational guidance, client contact records, payment investigations, liquidity reports, negotiated pricing, FX instructions, and paired clean/poisoned supplier messages.
- **FR-005**: Include Polish and US personal identifier examples, contact emails and phones, and account identifiers with exact document span annotations.
- **FR-006**: Include multiple malicious instruction families and quoted benign security guidance.
- **FR-007**: Store security annotations and retrieval questions outside the retrievable corpus, with links to stable source IDs.
- **FR-008**: Emit a relational database, portable structured exports, readable document files, and a manifest with counts and content hashes.
- **FR-009**: Validate generated models, references, source spans, and deterministic generation with automated tests.
- **FR-010**: Document future retrieval and control-layer integration without implementing embeddings, vector search, agent execution, or enforcement in this feature.

### Key Entities

- Client, contact, account, and transaction: linked fictional treasury records.
- Document: original narrative with classification, provenance, and source references.
- Annotation: evaluator-only factual evidence and offsets.
- Retrieval question: a query with relevant source IDs and supporting facts.
- Manifest: reproducibility metadata and artifact integrity information.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Default generation creates 12 clients, 24 contacts, 24 accounts, 1,000 transactions, and at least 96 documents.
- **SC-002**: Every generated relationship and annotated source span passes validation.
- **SC-003**: Two runs with the same configuration and pinned dependencies produce identical source exports and ground truth.
- **SC-004**: Every client has a PII document, a confidential document, and one clean/poisoned supplier pair; the complete corpus covers at least four attack families.
- **SC-005**: A developer can inspect sample records and documents without running any model or database server.

## Assumptions

- Corporate treasury and payments is the proposed domain, based on public Goldman Sachs Transaction Banking descriptions.
- User selected generator plus RAG design; retrieval execution is deferred.
- The user's explicit RAG request authorizes a demo-client extension to the earlier scope exclusion; middleware behavior remains governed by the existing [architecture](../../docs/architecture.md) and [contracts](../../contracts/README.md).
- Confidentiality and relevance labels are evaluation facts, not new enforcement policy or claimed detection capability.
- Synthetic identifiers can coincidentally resemble real identifiers; no generated contact or account is used operationally.
