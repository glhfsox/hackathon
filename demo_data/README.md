# Synthetic treasury source data

Generate fictional corporate treasury clients, contacts, accounts, payments, and original documents for a retrieval demo. Sources are inspired by the business topics of Goldman Sachs Transaction Banking, not by private Goldman Sachs records.

## Run

From the repository root, with Python 3.11 or newer:

```sh
python3 -m venv demo_data/.venv
demo_data/.venv/bin/python -m pip install -r demo_data/requirements-dev.txt
demo_data/.venv/bin/python -m demo_data generate --config demo_data/config.json --output demo_data/generated/treasury
demo_data/.venv/bin/python -m demo_data validate demo_data/generated/treasury
```

Default counts and seed live in [config.json](config.json). Copy it to configure another corpus. At least four clients and one payment per client are required. Use a new output directory for every run; the CLI refuses overwrites. A missing manifest means an incomplete run; choose another directory after correcting the reported error.

## Output

- `records/`: JSONL clients, contacts, accounts, and transactions. Amounts are integer minor units, always accompanied by currency.
- `documents/`: readable Markdown contact records, payment investigations, liquidity reports, pricing schedules, FX checklists, communication procedures, supplier emails, and public guides.
- `documents.jsonl`: metadata and body paths for ingestion.
- `corpus.sqlite3`: linked source tables and full document text, ready for SQL inspection without running a database server.
- `evaluation/`: exact PII/confidential/injection spans, clean/poisoned pairing, retrieval queries, relevant IDs, and supporting facts. Never index this directory or expose it to the agent.
- `manifest.json`: config, dependency/generator versions, counts, and hashes.

Example inspection:

```sh
sqlite3 demo_data/generated/treasury/corpus.sqlite3 'SELECT transaction_id, amount_minor, currency, status FROM transactions LIMIT 5;'
```

Default corpus has 12 clients, 24 contacts, 24 accounts, 1,000 payments, 100 documents, and 48 questions. Each client has an investigation referencing its actual payment plus one clean/poisoned supplier-message pair. Four attack families cover override, forged authority, exfiltration, and destructive tool instructions. A public security-training guide quotes attack phrases as a benign comparison. Longer liquidity notes support later chunk-boundary experiments.

The snapshot can be queried directly; future vector ingestion loads the JSONL and document exports into PostgreSQL. Field definitions: [contract](../contracts/demo-data.md). Chunking, embeddings, vector search, and guarded agent flow: [RAG design](../docs/rag-demo.md).

## Verify

```sh
demo_data/.venv/bin/python -m pytest demo_data/tests -q
demo_data/.venv/bin/ruff check demo_data
demo_data/.venv/bin/ruff format --check demo_data
```

Validation detects invalid models, broken links/spans, changed files, and SQLite/export disagreement. It does not prove retrieval relevance or middleware security behavior. Confidentiality is factual annotation and needs an explicit policy for verdicts. Faker-generated values are fictional fixtures and must never be used operationally. Pin requirements and seed for reproducibility; natural-language diversity is limited by the templates.
