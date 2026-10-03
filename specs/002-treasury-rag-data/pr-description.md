# Synthetic treasury data for the retrieval demo

The demo needs linked financial records and retrievable documents rather than standalone detector strings. Add a seeded Faker/Pydantic generator producing a fictional corporate treasury corpus with transactions, contact records, investigations, confidential liquidity/pricing notes, clean/poisoned supplier pairs, and public guides. Export JSONL, Markdown, SQLite, a checksummed manifest, and separate evaluation annotations/questions.

Runtime/setup: Python 3.11+; install `demo_data/requirements-dev.txt` into an isolated environment. Faker supplies localized values and Pydantic validates boundaries. Dependencies are pinned in a separate commit. No environment variables, paid APIs, database service, or model is needed to generate data. Generated bulk output is ignored; regenerate using the README.

Validation: Ruff check passed; Ruff format check passed (7 files); pytest passed (19 tests). Ran default generation and validation: 12 clients, 24 contacts, 24 accounts, 1,000 transactions, 100 documents, and 48 retrieval questions. SQLite integrity and foreign-key checks passed; inspected source documents and an investigation/payment join.

Scope: Generator and documented RAG design only. PostgreSQL/pgvector/Ollama ingestion, embedding, retrieval, and agent execution are deferred by user choice. Confidentiality labels need an explicit middleware policy before they imply verdicts. The corpus has template-limited variety.

Dependencies: The dataset contract and RAG design have a separate shared-document PR. This branch also includes the existing origin/dev scaffold because origin/main lacks the project's docs/contracts/spec-kit. Keep the implementation PR draft until the scaffold lands; the scaffold is inherited work rather than changes introduced by this feature. Do not merge without human instruction.
