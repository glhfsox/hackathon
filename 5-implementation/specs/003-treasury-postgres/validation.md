# Validation results

Executed on 2026-10-03 with Python 3.11.9, pinned runtime/dev requirements, and PostgreSQL 16 in the dedicated local Compose service. Backend/frontend code was not changed or exercised.

| Check | Actual result |
|-------|---------------|
| `ruff check demo_data` | `All checks passed!` |
| `ruff format --check demo_data` | `9 files already formatted` |
| Offline `pytest demo_data/tests -q` without database setting | `22 passed, 5 skipped in 3.80s` |
| Full pytest with `DEMO_TEST_DATABASE_URL` configured | `27 passed in 2.93s`; all five PostgreSQL integration tests executed |
| Validate existing default raw corpus | `status: valid`; original counts and source text preserved |
| First `load-postgres demo_data/generated/treasury` | `status: loaded` |
| Repeated `load-postgres demo_data/generated/treasury` | `status: already_loaded` with identical counts |
| PostgreSQL count inspection | 12 clients, 1,000 transactions, 100 documents |
| Investigation/payment join inspection | DOC-00002 -> TXN-000001 / PLN / held; DOC-00010 -> TXN-000002 / USD / held; DOC-00018 -> TXN-000003 / PLN / held |

Complete persisted counts: 12 clients, 24 contacts, 24 accounts, 1,000 transactions, 100 documents, 48 document/payment links. Full readback comparison succeeded before both load commits. Exact Unicode and malicious document text remain source data, never executed.

Integration tests cover complete source fidelity, absence of evaluator tables/columns, BIGINT money, safe repeat loading, conflicting-corpus rejection without changes, rollback of schema and rows after a mid-insert constraint error, account/client ownership enforcement, and CLI success. Offline tests include tampered-file preflight, schema validation, missing connection settings, and credential-safe error output.

The initial sandboxed database test attempt failed because localhost connections were disallowed. Its traceback exposed the generated local password. That local credential was rotated, the ignored `.env` updated, and the final full suite executed with sanitized output and connection permission. No secret was committed.

The obsolete `demo_data/generated/treasury/corpus.sqlite3` file and matching manifest entry were removed; all other source and evaluation files retained their original hashes and validation passed. The current corpus manifest inventories 107 source/evaluation files. Historical feature-002 implementation records remain historical; its run guide points to the current PostgreSQL workflow.

The local service remains available at 127.0.0.1:5433, database `treasury_demo`, schema `demo_data`. Credentials are in ignored `demo_data/.env`. No chunking, BGE dependency/model download, embedding calls, vector columns/extensions, or search was implemented. Publication remains deferred under the user's earlier refusal.
