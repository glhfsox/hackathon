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

The Compose service binds only localhost and persists its data in a named volume. Its pgvector-enabled image keeps PostgreSQL major version 16 and the existing volume. Stop it with `docker compose --env-file demo_data/.env -f demo_data/compose.yaml stop` when needed. The demo retrieval queries run read-only transactions; a separate database role with SELECT grants can be added during deployment. Evaluator files must never be exposed to the agent.

## Index and search documents

Install the optional RAG dependencies in the same environment. BGE-M3 runs directly in Python; the first use downloads about 2.3 GB of model weights. Keep its cache ignored locally:

```sh
demo_data/.venv/bin/python -m pip install -r demo_data/requirements-rag.txt
set -a
. demo_data/.env
set +a
export HF_HOME="$PWD/demo_data/.cache/huggingface"
demo_data/.venv/bin/python -m demo_data index
demo_data/.venv/bin/python -m demo_data search 'Why was the payment held?' --client-id CLI-0001 --transaction-id TXN-000001
```

`index` reads the PostgreSQL document bodies and creates `demo_data.document_chunks` plus `demo_data.rag_index`. Markdown headings define sections; short sections remain whole. Oversized sections split at paragraphs, with a small tokenizer-checked fallback for one oversized paragraph. No overlap or general recursive framework is needed for these fixtures. Exact character offsets, original text, section paths, hashes, and the model revision are retained. The 350-token limit includes embedding context; nothing is silently truncated.

Defaults live in [rag-config.json](rag-config.json). BGE-M3 produces normalized vectors with 1024 dimensions. PostgreSQL performs exact cosine search, filtering to the configured client plus global public guides before ranking; optional payment filtering uses source links. Search returns JSON with text, classification, IDs, similarity, and citation offsets. `evaluation/` is never indexed, and malicious/sensitive source text remains intact to test the middleware.

An unchanged repeat reports `already_indexed` and performs no document inference. After changing source/model/chunk settings, run `index --rebuild`; only derived chunks/index state are replaced, atomically. A failed rebuild leaves the previous index intact. Search refuses a stale or mismatched index. Use the same `--config` and `--schema` across indexing/search/agent.

To exercise poisoned supplier retrieval explicitly:

```sh
demo_data/.venv/bin/python -m demo_data search 'Supplier invoice clarification processing note instructions' --client-id CLI-0001 --transaction-id TXN-000001 --k 10
```

Check that returned text actually includes DOC-00008's malicious processing note before interpreting a security verdict. Both clean/poisoned documents are retained; top results alone are not an attack label.

## Guarded agent and pending backend connection

The small agent loop is implemented, with two tools: `search_documents` and `query_transactions`. Exact payment records and per-currency integer credit/debit totals come from fixed SQL, not embeddings. Every model request goes through the control layer, and every tool execution first calls `/v1/tools/check` with validated canonical arguments and conversation history. Blocks, malformed responses, and unreachable guards stop execution.

**Live backend/Ollama chat-model connection is not implemented or verified in this checkout**, as requested. Your colleague still needs to run the backend/model, authorize the model and both tool names in its policy, and provide the caller key and endpoint. This is distinct from BGE-M3, which is used for embeddings only. Current status and remaining work are explicit in [the handoff](../docs/rag-handoff.md).

Once that backend configuration exists, set `DEMO_CONTROL_BASE_URL` (the control-layer URL ending in `/v1`), `DEMO_API_KEY`, and `DEMO_AGENT_MODEL` in your ignored environment, then run:

```sh
demo_data/.venv/bin/python -m demo_data agent 'Why is TXN-000001 held?' --client-id CLI-0001
```

The agent reports `answered`, `blocked`, `error`, or `limit`, with the answer/reason, decisions, and retrieved source IDs. It never calls the chat model directly as a fallback. Client scope is trusted operator configuration; this CLI does not implement a production authorization service. Classifications are passed to the middleware, with no new confidentiality-enforcement claim.

## Output

- `records/`: JSONL clients, contacts, accounts, and transactions. Amounts are integer minor units, always accompanied by currency.
- `documents/`: readable Markdown contact records, payment investigations, liquidity reports, pricing schedules, FX checklists, communication procedures, supplier emails, and public guides.
- `documents.jsonl`: metadata and body paths for ingestion.
- `evaluation/`: exact PII/confidential/injection spans, clean/poisoned pairing, retrieval queries, relevant IDs, and supporting facts. Never index this directory or expose it to the agent.
- `manifest.json`: config, dependency/generator versions, counts, and hashes.

Default corpus has 12 clients, 24 contacts, 24 accounts, 1,000 payments, 100 documents, and 48 questions. Each client has an investigation referencing its actual payment plus one clean/poisoned supplier-message pair. Four attack families cover override, forged authority, exfiltration, and destructive tool instructions. A public security-training guide quotes attack phrases as a benign comparison. Longer liquidity notes support later chunk-boundary experiments.

PostgreSQL holds raw records, full documents, chunks, and vectors. Field definitions: [source contract](../contracts/demo-data.md) and [RAG tool contract](../contracts/rag-demo.md). Design: [RAG design](../docs/rag-demo.md).

## Verify

```sh
demo_data/.venv/bin/python -m pytest demo_data/tests -q
demo_data/.venv/bin/ruff check demo_data
demo_data/.venv/bin/ruff format --check demo_data
```

Without `DEMO_TEST_DATABASE_URL`, database integration tests are explicitly skipped; ordinary tests run offline without downloading a model. With that variable set, PostgreSQL tests use newly generated namespaces and remove only their own objects. An unreachable configured server fails tests rather than skipping them.

The opt-in middleware checks additionally need Python 3.12 (the backend's version), `DEMO_BACKEND_PATH` pointing to a working backend checkout, its dependencies installed in the test environment, and an already indexed default demo corpus in the test database. They use real BGE-M3/pgvector retrieval and the real middleware with explicit temporary policies and scripted upstream. No live chat model/Jev or production policy is used. These tests read the existing default demo schema and write only temporary policy/audit files. They separately document the default feed's unmatched attack phrase and verify blocking with a temporary fixture signature; see [handoff](../docs/rag-handoff.md).

```sh
export DEMO_BACKEND_PATH=/absolute/path/to/working/backend
demo_data/.venv/bin/python -m pytest demo_data/tests/test_middleware.py -q
```

Actual checks run for this feature are recorded in [validation.md](../specs/004-treasury-rag/validation.md).

Offline validation detects invalid models, broken links/spans, and changed files. PostgreSQL loading also verifies exact persisted source values and links. Neither proves retrieval relevance or middleware security behavior. Confidentiality is factual annotation and needs an explicit policy for verdicts. Faker-generated values are fictional fixtures and must never be used operationally. Pin requirements and seed for reproducibility; natural-language diversity is limited by the templates.
