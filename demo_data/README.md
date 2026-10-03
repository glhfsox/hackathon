# Synthetic treasury source data

Generate fictional corporate treasury clients, contacts, accounts, payments, and original documents for a retrieval demo. Sources are inspired by the business topics of Goldman Sachs Transaction Banking, not by private Goldman Sachs records.

## Generate and validate source files

From the repository root, with Python 3.11 or newer:

```sh
python3 -m venv demo_data/.venv
demo_data/.venv/bin/python -m pip install -r demo_data/requirements-dev.txt
demo_data/.venv/bin/python -m demo_data generate --config demo_data/config.json --output demo_data/generated/treasury
demo_data/.venv/bin/python -m demo_data validate demo_data/generated/treasury
```

Default counts and seed live in [config.json](config.json). Copy it to configure another corpus. At least four clients and one payment per client are required. Use a new output directory for every run; the CLI refuses overwrites. A missing manifest means an incomplete run; choose another directory after correcting the reported error.

Generation and offline validation do not require a database. If `demo_data/generated/treasury` already exists, use those source files rather than generating them again.

## Load PostgreSQL

The loader reads the source files and stores all records and complete Markdown text in PostgreSQL. It does not store only document paths. Evaluation files remain outside the database.

An existing PostgreSQL server can be used by setting `DEMO_DATABASE_URL`. For the bundled local service, use `demo_data/.env` (ignored by git). If it does not exist, copy [.env.example](.env.example) and fill in `POSTGRES_PASSWORD` and `DEMO_DATABASE_URL`. The local service uses database/user `treasury_demo` and port `5433` by default. The connection URL has this shape:

```text
postgresql://treasury_demo:<URL-encoded-password>@127.0.0.1:5433/treasury_demo
```

If `DEMO_POSTGRES_PORT` is changed, update the URL accordingly. `DEMO_TEST_DATABASE_URL` is optional and used only for integration tests.

```sh
docker compose --env-file demo_data/.env -f demo_data/compose.yaml up -d --wait
set -a
. demo_data/.env
set +a
demo_data/.venv/bin/python -m demo_data load-postgres demo_data/generated/treasury
```

The command reports `loaded` and source-table counts. Repeating it reports `already_loaded` after checking every stored source value and link. A different corpus in populated tables is rejected. Use `--schema another_corpus` to load a separate corpus without replacing data. Schema creation and all inserts commit together; errors roll back the transaction. The loader never drops or truncates populated tables.

Inspect the default data:

```sh
docker compose --env-file demo_data/.env -f demo_data/compose.yaml exec -T postgres psql -U treasury_demo -d treasury_demo -c 'SELECT transaction_id, amount_minor, currency, status FROM demo_data.transactions LIMIT 5;'
```

The Compose service binds only localhost and persists its data in a named volume. Stop it with `docker compose --env-file demo_data/.env -f demo_data/compose.yaml stop` when needed. Using a separate application role with source-only SELECT permissions is part of the later agent integration; evaluator files must not be exposed to that role.

## Output

- `records/`: JSONL clients, contacts, accounts, and transactions. Amounts are integer minor units, always accompanied by currency.
- `documents/`: readable Markdown contact records, payment investigations, liquidity reports, pricing schedules, FX checklists, communication procedures, supplier emails, and public guides.
- `documents.jsonl`: metadata and body paths for ingestion.
- `evaluation/`: exact PII/confidential/injection spans, clean/poisoned pairing, retrieval queries, relevant IDs, and supporting facts. Never index this directory or expose it to the agent.
- `manifest.json`: config, dependency/generator versions, counts, and hashes.

Default corpus has 12 clients, 24 contacts, 24 accounts, 1,000 payments, 100 documents, and 48 questions. Each client has an investigation referencing its actual payment plus one clean/poisoned supplier-message pair. Four attack families cover override, forged authority, exfiltration, and destructive tool instructions. A public security-training guide quotes attack phrases as a benign comparison. Longer liquidity notes support later chunk-boundary experiments.

PostgreSQL now holds the raw records and full documents. Field definitions: [contract](../contracts/demo-data.md). Chunking, BGE-M3 embeddings, pgvector storage/search, and guarded agent flow remain the next step: [RAG design](../docs/rag-demo.md).

## Verify

```sh
demo_data/.venv/bin/python -m pytest demo_data/tests -q
demo_data/.venv/bin/ruff check demo_data
demo_data/.venv/bin/ruff format --check demo_data
```

Without `DEMO_TEST_DATABASE_URL`, the database integration tests are explicitly skipped; the remaining tests run offline. To run the complete suite against the local service, set that variable to its connection URL before invoking pytest. Tests use newly generated namespaces and remove only their own test objects. An unreachable configured server fails tests rather than skipping them.

Offline validation detects invalid models, broken links/spans, and changed files. PostgreSQL loading also verifies exact persisted source values and links. Neither proves retrieval relevance or middleware security behavior. Confidentiality is factual annotation and needs an explicit policy for verdicts. Faker-generated values are fictional fixtures and must never be used operationally. Pin requirements and seed for reproducibility; natural-language diversity is limited by the templates.
