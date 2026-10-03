# Validation results

Executed on 2026-10-03 using Python 3.11.9 and a temporary virtual environment at `/private/tmp/treasury-data-venv`. Dependencies match the pinned runtime/dev requirement files. Backend and frontend were not changed or exercised.

| Command | Actual result |
|---------|---------------|
| `ruff check demo_data` | `All checks passed!` |
| `ruff format --check demo_data` | `7 files already formatted` |
| `python -m pytest demo_data/tests -q` | `19 passed in 0.56s` |
| `python -m demo_data generate --config demo_data/config.json --output demo_data/generated/treasury` | `status: generated`; counts below |
| `python -m demo_data validate demo_data/generated/treasury` | `status: valid`; same counts |
| SQLite `PRAGMA integrity_check` | `ok` |
| SQLite `PRAGMA foreign_key_check` | Empty list |

Counts: 12 clients, 24 contacts, 24 accounts, 1,000 transactions, 100 documents, 100 ground-truth records, 48 questions. The manifest hashes 108 artifacts. Source documents and a payment-investigation SQL join were inspected.

Tests verify deterministic source/SQLite bytes, changes with a different seed, account/transaction references, agreement of narrative amounts and liquidity calculations, PESEL/IBAN checksums and SSN validity, attack families, matching pair metadata, evaluator isolation, CLI failures, config/model rejection, overwrite refusal, source tampering, and snapshot disagreement.

Generated files stay local under the ignored `demo_data/generated/` directory and can be reproduced. The previous generator smoke output was retained under `treasury-initial/`; `treasury/` is the final source corpus.

No embeddings, vector ranking, model-based detection, or full agent flow was run; these remain design-only as requested. Template diversity is limited. Confidentiality annotations do not establish a middleware enforcement capability.

Delivery: Local commits and a separate docs branch are prepared. The user declined the sync/push execution approval; neither branch was published and no PR was opened. PR text is available in `pr-description.md`.
