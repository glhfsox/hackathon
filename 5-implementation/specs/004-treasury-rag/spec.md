# Feature Specification: Treasury RAG demo

**Feature Branch**: `feat/treasury-rag`
**Created**: 2026-10-03
**Status**: Ready for implementation
**Input**: Implement Documents → chunks → BGE-M3 embeddings → PostgreSQL/pgvector search → agent → control layer. Use structure-based chunking and avoid overengineering this test client.

## User Scenarios & Testing

### User Story 1 — Search the existing document corpus (Priority: P1)

As a developer, I can prepare the existing treasury documents for semantic retrieval and search their contents without regenerating or changing the sources.

**Why this priority**: Retrieved PII, confidential facts, and malicious instructions are the inputs needed to exercise the middleware.
**Independent Test**: Index the corpus, search an investigation with its payment reference, and verify returned source citations against the original document.

**Acceptance Scenarios**:

1. **Given** loaded documents, **when** indexing runs, **then** all nonempty documents produce traceable, bounded chunks and complete dense vectors, with evaluator annotations excluded.
2. **Given** an unchanged source corpus and settings, **when** indexing repeats, **then** existing embeddings are reused without duplicate chunks.
3. **Given** a client-scoped query, **when** searching, **then** only that client's documents and global public guides are eligible; optional payment filtering retains relevant links.
4. **Given** oversized sections or Polish characters, **when** chunking, **then** exact original text and character offsets are preserved without silent truncation.

### User Story 2 — Run a guarded treasury agent (Priority: P1)

As a developer, I can ask a small agent payment questions using retrieved documents and exact transaction records, while every model turn and tool execution goes through the middleware.

**Why this priority**: The scored product is the middleware; the agent exists to deliver realistic tool results to it.
**Independent Test**: A scripted model requests retrieval, the tool guard allows it, and the returned text reaches the middleware before the model's final answer.

**Acceptance Scenarios**:

1. **Given** an allowed tool call, **when** the agent runs it, **then** it first obtains a valid tool-guard response, executes only the approved arguments, and sends the result in the next full conversation.
2. **Given** a proxy or guard block, malformed response, or unavailable guard, **when** the agent continues, **then** it executes no denied tool and reports the stop reason.
3. **Given** transaction queries, **when** exact records or totals are requested, **then** results use fixed bounded queries, integer amounts, and separate currencies.
4. **Given** an agent attempting another client or arbitrary SQL, **when** validating arguments, **then** those arguments are rejected before database access.

### User Story 3 — Verify realistic middleware inputs (Priority: P2)

As a developer, I can confirm that sensitive and poisoned documents were retrieved and observe actual control-layer decisions without using evaluator labels as retrieval features.

**Independent Test**: Run a documented poisoned-supplier query and compare retrieved IDs with evaluator-only labels after retrieval; verify PII redaction and injection blocking with explicit test policy settings.

**Acceptance Scenarios**:

1. **Given** PII-containing retrieval, **when** an enforced redaction policy handles the tool result, **then** the upstream model receives redacted content and the decision is observable.
2. **Given** a retrieved malicious processing note, **when** an explicit blocking policy applies, **then** the attack is stopped and no further model-driven action executes.
3. **Given** confidential retrieval, **when** examining results, **then** source classification is present; no unsupported confidentiality-enforcement claim is made.

### Edge Cases

- Missing pgvector extension, model download failure, nonfinite/zero/wrong-size vectors, unknown client, empty query, or absent index produce explicit errors.
- A failed rebuild leaves the previous derived index usable; concurrent source edits during embedding are detected before commit.
- Changed source/model/chunk settings require a deliberate rebuild, preventing mismatched query and document embeddings.
- Code fences containing heading-like lines do not create false sections; one oversized paragraph still fits the embedding budget.
- Step limits terminate unfinished agent runs with an explicit status, not a fabricated answer.

## Requirements

### Functional Requirements

- **FR-001**: Prepare documents already stored in the source database, preserving original sources and excluding evaluator-only data.
- **FR-002**: Split at document structure first; use only a small bounded fallback for oversized sections.
- **FR-003**: Preserve document IDs, section paths, exact original text ranges, input hashes, source hashes, and model identity.
- **FR-004**: Use the same pinned multilingual encoder for documents and queries and reject silent truncation or invalid vectors.
- **FR-005**: Store and search vectors with the existing source database; use exact search for this small corpus.
- **FR-006**: Scope tools to an operator-configured client; the model cannot expand that scope.
- **FR-007**: Expose only document search and fixed, bounded transaction queries, with per-currency integer totals.
- **FR-008**: Send all agent model requests through the control layer and obtain a valid guard approval before every tool execution.
- **FR-009**: Validate external responses and tool arguments and fail closed on invalid or unavailable control-layer responses.
- **FR-010**: Record source IDs, control decisions, and completion status for review; preserve classification in tool results.
- **FR-011**: Supply runnable setup, indexing, search, agent, and verification instructions and meaningful automated checks.

### Key Entities

- **Chunk**: Exact source range, section context, identity, and bounded embedding input.
- **Index identity**: Source fingerprint, chunking configuration, encoder revision, and dimensions.
- **Search result**: Ranked cited source text and classification scoped to the configured client.
- **Agent run**: Question, model/tool conversation, guard decisions, retrieved IDs, and final/blocked/error/limit status.

## Success Criteria

### Measurable Outcomes

- **SC-001**: All 100 source documents are indexed with verifiable citations and no source changes or evaluator leakage.
- **SC-002**: Repeating unchanged indexing makes zero document-embedding calls and creates zero duplicate chunks.
- **SC-003**: Every executed tool has an earlier guard approval; blocked/unavailable guards execute zero tools.
- **SC-004**: Real retrieval, PII redaction, and retrieved-injection blocking are demonstrated separately and their evidence recorded.
- **SC-005**: Deterministic tests and database integration checks pass with documented commands.

## Assumptions

- This is a CLI demo client, not a production authorization service or a new middleware implementation. Operator-selected client scope is trusted configuration; all classifications within that scope remain available for security tests.
- The model and control-layer URL/key are configurable; scripted upstream responses are suitable for repeatable integration verification.
- The working middleware remains on its own branch. Verify against a separate copy instead of merging or editing teammates' backend work.
- The existing synthetic raw corpus and PostgreSQL volume are retained. The user explicitly extends the previously deferred demo scope.
- No hybrid sparse retrieval, reranker, approximate vector index, recursive chunking framework, arbitrary SQL, shell execution, or new confidentiality policy is included.

See [design](../../docs/rag-demo.md), [dataset contract](../../contracts/demo-data.md), and [middleware HTTP contract](../../contracts/http-api.md).
