# Quickstart

Follow [demo_data/README.md](../../demo_data/README.md) to configure credentials, start PostgreSQL, and load existing source files. The CLI validates hashes, records, and annotations before connecting. Generation and offline validation remain usable without a server.

Run `python -m pytest demo_data/tests -q` and Ruff checks. Set `DEMO_TEST_DATABASE_URL` to a dedicated test database to run integration tests; those tests create isolated namespaces and clean up only their own test objects.

Prove the migration by loading the default corpus twice and comparing stored source counts, exact document text (including Polish diacritics), and investigation/payment joins. Tampered files, different populated corpora, and mid-insert constraint errors must produce explicit failures without partial writes.
