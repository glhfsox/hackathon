import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from demo_data.generate import generate
from demo_data.models import GenerationConfig
from demo_data.postgres import load_postgres
from demo_data.rag import TreasuryTools, index_documents
from demo_data.rag_models import RagConfig, SearchArgs, TransactionArgs
from demo_data.storage import export
from psycopg import sql


class FakeEncoder:
    def __init__(self, config: RagConfig = RagConfig()):
        self.config = config
        self.calls = 0

    def count_tokens(self, text: str) -> int:
        return len(text.split()) + 2

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        # PII contact sections are uniquely closest to the contact query in these tests.
        vectors = []
        for text in texts:
            vector = [0.0] * self.config.dimensions
            vector[0 if "contact" in text.lower() else 1] = 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def rag_database(tmp_path: Path):
    database_url = os.environ.get("DEMO_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set DEMO_TEST_DATABASE_URL to run PostgreSQL integration tests")
    pytest.importorskip(
        "pgvector", reason="install requirements-rag.txt for vector integration tests"
    )
    schema = "test_rag_" + uuid4().hex[:16]
    config = GenerationConfig(
        schema_version=1,
        seed=20261003,
        clients=4,
        transactions=32,
        reference_date=date(2026, 10, 3),
    )
    corpus = generate(config)
    path = tmp_path / "corpus"
    export(corpus, config, path)
    load_postgres(path, database_url, schema)
    try:
        yield database_url, schema, corpus
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_index_reuses_embeddings_and_search_filters_sources(rag_database) -> None:
    url, schema, corpus = rag_database
    encoder = FakeEncoder()
    first = index_documents(url, encoder.config, schema, encoder=encoder)
    assert first["status"] == "indexed" and first["documents"] == len(corpus.documents)
    calls = encoder.calls
    assert (
        index_documents(url, encoder.config, schema, encoder=encoder)["status"] == "already_indexed"
    )
    assert encoder.calls == calls
    tools = TreasuryTools(url, "CLI-0001", encoder.config, schema, encoder=encoder)
    hits = tools.search_documents(SearchArgs(query="authorized contacts", k=10))["chunks"]
    assert hits and all(hit["client_id"] in (None, "CLI-0001") for hit in hits)
    assert hits[0]["document_id"] == corpus.documents[0].document_id
    original = {doc.document_id: doc.text for doc in corpus.documents}
    for hit in hits:
        assert hit["text"] == original[hit["document_id"]][hit["char_start"] : hit["char_end"]]
    linked = tools.search_documents(SearchArgs(query="payment", k=10, transaction_id="TXN-000001"))[
        "chunks"
    ]
    allowed = {doc.document_id for doc in corpus.documents if "TXN-000001" in doc.transaction_ids}
    assert linked and all(hit["document_id"] in allowed for hit in linked)
    with psycopg.connect(url) as conn:
        chunks = conn.execute(
            sql.SQL("SELECT document_id, text FROM {} ORDER BY chunk_id").format(
                sql.Identifier(schema, "document_chunks")
            )
        ).fetchall()
    for doc in corpus.documents:
        assert "".join(text for doc_id, text in chunks if doc_id == doc.document_id) == doc.text


@pytest.mark.postgres
def test_rebuild_identity_and_failed_embedding_preserve_index(rag_database) -> None:
    url, schema, _ = rag_database
    encoder = FakeEncoder()
    index_documents(url, encoder.config, schema, encoder=encoder)
    changed = RagConfig(max_chunk_tokens=400)
    with pytest.raises(ValueError, match="rebuild"):
        index_documents(url, changed, schema, encoder=FakeEncoder(changed))

    class BadEncoder(FakeEncoder):
        def encode(self, texts):
            return [[0.0]] * len(texts)

    with pytest.raises(ValueError, match="dimensions"):
        index_documents(url, changed, schema, rebuild=True, encoder=BadEncoder(changed))
    assert (
        index_documents(url, encoder.config, schema, encoder=encoder)["status"] == "already_indexed"
    )
    assert (
        index_documents(url, changed, schema, rebuild=True, encoder=FakeEncoder(changed))["status"]
        == "indexed"
    )


@pytest.mark.postgres
def test_mid_write_failure_rolls_back_deleted_chunks_and_index_identity(rag_database) -> None:
    url, schema, _ = rag_database
    encoder = FakeEncoder()
    first = index_documents(url, encoder.config, schema, encoder=encoder)
    with psycopg.connect(url) as conn:
        # NOT VALID leaves existing rows intact but rejects the rebuild after its DELETE.
        conn.execute(
            sql.SQL(
                "ALTER TABLE {} ADD CONSTRAINT reject_new_chunks CHECK (token_count < 1) NOT VALID"
            ).format(sql.Identifier(schema, "document_chunks"))
        )
    changed = RagConfig(max_chunk_tokens=400)
    with pytest.raises(psycopg.errors.CheckViolation):
        index_documents(url, changed, schema, rebuild=True, encoder=FakeEncoder(changed))
    repeated = index_documents(url, encoder.config, schema, encoder=encoder)
    assert repeated == {
        "status": "already_indexed",
        "documents": first["documents"],
        "chunks": first["chunks"],
    }


@pytest.mark.postgres
def test_stale_sources_and_unknown_clients_are_rejected(rag_database) -> None:
    url, schema, _ = rag_database
    encoder = FakeEncoder()
    index_documents(url, encoder.config, schema, encoder=encoder)
    with pytest.raises(ValueError, match="unknown client"):
        TreasuryTools(url, "CLI-9999", encoder.config, schema, encoder=encoder)
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL("UPDATE {} SET text = text || 'changed' WHERE document_id = %s").format(
                sql.Identifier(schema, "documents")
            ),
            ("DOC-00001",),
        )
    tools = TreasuryTools(url, "CLI-0001", encoder.config, schema, encoder=encoder)
    with pytest.raises(ValueError, match="rebuild"):
        tools.search_documents(SearchArgs(query="contacts"))


@pytest.mark.postgres
def test_transaction_tool_matches_exact_client_rows_and_totals(rag_database) -> None:
    url, schema, corpus = rag_database
    tools = TreasuryTools(url, "CLI-0001", RagConfig(), schema, encoder=FakeEncoder())
    output = tools.query_transactions(TransactionArgs(limit=1))
    expected = [tx for tx in corpus.transactions if tx.client_id == "CLI-0001"]
    assert output["matching_count"] == len(expected) > len(output["transactions"]) == 1
    for total in output["totals_by_currency"]:
        for direction in ("credit", "debit"):
            assert total[direction + "_minor"] == sum(
                tx.amount_minor
                for tx in expected
                if tx.currency == total["currency"] and tx.direction == direction
            )
    exact = tools.query_transactions(TransactionArgs(transaction_id="TXN-000001"))
    assert exact["transactions"] == [corpus.transactions[0].model_dump(mode="json")]
    assert (
        tools.query_transactions(TransactionArgs(transaction_id="TXN-000002"))["transactions"] == []
    )


@pytest.mark.parametrize(
    "args",
    [
        {"query": "x", "client_id": "CLI-9999"},
        {"query": " "},
        {"query": "x", "k": 100},
        {"query": "x", "k": "5"},
    ],
)
def test_search_arguments_cannot_expand_scope_or_coerce(args) -> None:
    with pytest.raises(ValueError):
        SearchArgs.model_validate_json(json.dumps(args))


def test_transactions_reject_sql_and_unknown_filters() -> None:
    with pytest.raises(ValueError):
        TransactionArgs.model_validate({"sql": "SELECT * FROM contacts"})
