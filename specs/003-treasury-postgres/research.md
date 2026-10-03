# Research

- Decision: Psycopg 3 with binary extra; parameterized inserts within a connection transaction.
- Rationale: Small driver dependency; no ORM or migration framework needed for a dedicated demo schema. Connection context commits on success and rolls back on exceptions.
- Alternatives: COPY is useful at larger scale but unnecessary for 1,000 rows; direct SQL interpolation is rejected; SQLite removed by user request.
- Sources: [transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html), [parameters](https://www.psycopg.org/psycopg3/docs/basic/params.html).

- Decision: Dedicated `demo_data` schema, BIGINT money, TIMESTAMPTZ dates, TEXT full documents, and normalized document/payment links.
- Rationale: Avoid middleware table collisions and preserve exact source values and ownership relationships. No vector extension or empty vector tables in this step.
- Alternatives: A database per corpus adds operational overhead; mutable upserts silently change data and are unnecessary for initial ingestion.
- Source: [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html).

- Decision: Identical repeated load is accepted after complete readback comparison; different data in populated tables is rejected. Source validation happens before connection/writes.
- Rationale: Reproducible demo without duplicate rows or automatic destructive replacement.
- Alternatives: Truncate/reload is destructive; ON CONFLICT DO NOTHING can silently leave a mixed corpus.

- Decision: PostgreSQL 16 Docker Compose service, with password supplied outside git. Existing installed image supports an isolated local verification instance.
- Rationale: Reusable setup with persistent volume and localhost-only port. Driver reads `DEMO_DATABASE_URL`; tests read `DEMO_TEST_DATABASE_URL`.
