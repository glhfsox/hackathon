# Quickstart validation

From the repository root:

```sh
python3 -m venv demo_data/.venv
demo_data/.venv/bin/python -m pip install -r demo_data/requirements-dev.txt
demo_data/.venv/bin/python -m demo_data generate --config demo_data/config.json --output demo_data/generated/treasury
demo_data/.venv/bin/python -m demo_data validate demo_data/generated/treasury
demo_data/.venv/bin/python -m pytest demo_data/tests -q
demo_data/.venv/bin/ruff check demo_data
demo_data/.venv/bin/ruff format --check demo_data
```

Default counts: 12 clients, 24 contacts, 24 accounts, 1,000 transactions, 100 documents, 48 retrieval questions. Output must be a new directory. Inspect Markdown under `documents/` and `corpus.sqlite3` with a SQLite browser. Shapes: [contract](../../contracts/demo-data.md). Later ingestion: [design](../../docs/rag-demo.md).

Validation checks models, hashes, relationships, spans, and database snapshot. It does not measure retrieval or middleware detection quality.
