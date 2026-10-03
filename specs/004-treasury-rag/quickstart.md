# Validation guide

Use the canonical commands in [demo_data/README.md](../../demo_data/README.md).

1. Keep the existing PostgreSQL volume and upgrade the service image to pgvector with the same PostgreSQL major version. Confirm 100 documents and 1000 transactions remain.
2. Install the optional RAG requirements, source the ignored `.env`, and run `index`. Repeat and require `already_indexed` without inference.
3. Search client CLI-0001 with payment TXN-000001. Verify citations against the stored document text, then separately verify the supplier processing-note query retrieves DOC-00008.
4. Configure the middleware caller to allow the two RAG tools and select an upstream model. Run `agent` through its URL/key; verify decisions and source IDs.
5. Run pytest and Ruff; with the test database env configured, run integration checks. Verify a separate real middleware copy with a scripted upstream for redaction/injection proof.

Actual output, dependency versions, and remaining limits are recorded in [validation.md](validation.md).
