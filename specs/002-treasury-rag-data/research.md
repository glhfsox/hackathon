# Research

## Domain

- Decision: Fictional corporate treasury and payments.
- Rationale: Goldman Sachs describes TxB as serving corporate treasurers and payments executives with payments, FX, and liquidity services. This links transfers to onboarding, investigations, pricing, and liquidity documents.
- Alternatives considered: M&A (confidential narratives but weaker payment linkage); wealth management (more portfolio modeling).
- Sources: [businesses](https://www.goldmansachs.com/what-we-do/our-businesses), [TxB](https://www.goldmansachs.com/what-we-do/transaction-banking), accessed 2026-10-03. Topics inspire original synthetic material; no proprietary Goldman Sachs schema or documents are used.

## Generation

- Decision: Seeded Faker plus deterministic narrative templates.
- Rationale: Localized values with cross-record consistency and exact annotations. Pin Faker because seed reproducibility depends on version.
- Alternatives considered: AI-only generation (less reproducible); handwritten corpus (less scalable).
- Source: [Faker seeding](https://faker.readthedocs.io/en/stable/index.html).

## Retrieval architecture

- Decision: Future PostgreSQL + pgvector; local Ollama embeddings; exact vector search initially; SQL for amounts and aggregation.
- Rationale: One database retains relations and vectors. Same embedding model and dimensions for ingestion and queries.
- Alternatives considered: SQLite + Chroma/Qdrant (two stores); paid embeddings (outside constraints); RAG frameworks (unnecessary abstraction).
- Sources: [pgvector](https://github.com/pgvector/pgvector), [Ollama embed API](https://docs.ollama.com/api/embed).

## Evaluation isolation

- Decision: Separate annotations, pair labels, questions, and answers from indexed text/metadata.
- Rationale: Avoid inflated evaluation through label leakage. Clean/poisoned pairs share ordinary retrieval metadata.
- Alternatives considered: Attack flags on source documents (rejected).
