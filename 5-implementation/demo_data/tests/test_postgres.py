import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from demo_data import postgres
from demo_data.__main__ import main
from demo_data.generate import generate
from demo_data.models import Corpus, GenerationConfig
from demo_data.postgres import load_postgres
from demo_data.storage import export
from psycopg import sql


@pytest.fixture
def raw_corpus(tmp_path: Path) -> tuple[Path, Corpus, GenerationConfig]:
    config = GenerationConfig(
        schema_version=1,
        seed=20261003,
        clients=4,
        transactions=32,
        reference_date=date(2026, 10, 3),
    )
    corpus = generate(config)
    directory = tmp_path / "corpus"
    export(corpus, config, directory)
    return directory, corpus, config


@pytest.fixture
def database():
    database_url = os.environ.get("DEMO_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set DEMO_TEST_DATABASE_URL to run PostgreSQL integration tests")
    schema = "test_treasury_" + uuid4().hex[:16]
    try:
        yield database_url, schema
    finally:
        # Each test owns only this newly generated namespace, never the demo schema.
        with psycopg.connect(database_url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_real_load_preserves_all_source_values_and_excludes_evaluation(
    raw_corpus, database
) -> None:
    directory, corpus, _ = raw_corpus
    database_url, schema = database
    result = load_postgres(directory, database_url, schema)
    assert result == {
        "status": "loaded",
        "schema": schema,
        "counts": {
            "clients": 4,
            "contacts": 8,
            "accounts": 8,
            "transactions": 32,
            "documents": 36,
            "document_transactions": 16,
        },
    }
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            sql.SQL("SELECT document_id, text FROM {} ORDER BY document_id").format(
                sql.Identifier(schema, "documents")
            )
        ).fetchall()
        assert rows == [(doc.document_id, doc.text) for doc in corpus.documents]
        names = conn.execute(
            sql.SQL("SELECT name FROM {} ORDER BY client_id").format(
                sql.Identifier(schema, "clients")
            )
        ).fetchall()
        assert names == [(client.name,) for client in corpus.clients]
        assert any(any(ord(c) > 127 for c in name) for (name,) in names)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (schema,),
            )
        }
        assert tables == {*postgres.SOURCE_MODELS, "document_transactions"}
        column_names = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s",
                (schema,),
            )
        }
        assert not column_names & {"attack_family", "pair_id", "variant", "answer_facts"}
        assert conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'transactions' "
            "AND column_name = 'amount_minor'",
            (schema,),
        ).fetchone() == ("bigint",)


@pytest.mark.postgres
def test_identical_reload_and_different_corpus_refusal(raw_corpus, database, tmp_path) -> None:
    directory, corpus, config = raw_corpus
    database_url, schema = database
    first = load_postgres(directory, database_url, schema)
    second = load_postgres(directory, database_url, schema)
    assert second["status"] == "already_loaded" and second["counts"] == first["counts"]
    other_config = config.model_copy(update={"seed": config.seed + 1})
    other = tmp_path / "other"
    export(generate(other_config), other_config, other)
    with pytest.raises(ValueError, match="PostgreSQL/source mismatch"):
        load_postgres(other, database_url, schema)
    assert load_postgres(directory, database_url, schema)["status"] == "already_loaded"
    with psycopg.connect(database_url) as conn:
        assert conn.execute(
            sql.SQL("SELECT name FROM {} ORDER BY client_id").format(
                sql.Identifier(schema, "clients")
            )
        ).fetchall() == [(client.name,) for client in corpus.clients]


@pytest.mark.postgres
def test_mid_insert_failure_rolls_back_schema_and_rows(raw_corpus, database, monkeypatch) -> None:
    directory, _, _ = raw_corpus
    database_url, schema = database
    original_rows = postgres.source_rows

    def invalid_contacts(corpus, table, model):
        rows = original_rows(corpus, table, model)
        if table == "contacts":
            bad = list(rows[0])
            bad[postgres.columns(model).index("email")] = None
            rows[0] = tuple(bad)
        return rows

    monkeypatch.setattr(postgres, "source_rows", invalid_contacts)
    with pytest.raises(psycopg.errors.NotNullViolation):
        load_postgres(directory, database_url, schema)
    with psycopg.connect(database_url) as conn:
        assert (
            conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()
            is None
        )


@pytest.mark.postgres
def test_database_enforces_transaction_ownership(raw_corpus, database) -> None:
    directory, corpus, _ = raw_corpus
    database_url, schema = database
    load_postgres(directory, database_url, schema)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(database_url) as conn:
        conn.execute(
            sql.SQL("UPDATE {} SET client_id = %s WHERE transaction_id = %s").format(
                sql.Identifier(schema, "transactions")
            ),
            (corpus.clients[1].client_id, corpus.transactions[0].transaction_id),
        )
    assert load_postgres(directory, database_url, schema)["status"] == "already_loaded"


@pytest.mark.postgres
def test_load_cli_uses_environment_and_reports_success(
    raw_corpus, database, monkeypatch, capsys
) -> None:
    directory, _, _ = raw_corpus
    database_url, schema = database
    monkeypatch.setenv("DEMO_DATABASE_URL", database_url)
    assert main(["load-postgres", str(directory), "--schema", schema]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "loaded" and response["counts"]["documents"] == 36
    assert main(["load-postgres", str(directory), "--schema", schema]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "already_loaded"


def test_tampered_files_fail_before_connecting(raw_corpus, monkeypatch) -> None:
    directory, corpus, _ = raw_corpus
    path = directory / "documents" / f"{corpus.documents[0].document_id}.md"
    path.write_text(path.read_text() + "changed")

    def unexpected_connection(*args, **kwargs):
        pytest.fail("source validation must happen before opening PostgreSQL")

    monkeypatch.setattr(postgres.psycopg, "connect", unexpected_connection)
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_postgres(directory, "dbname=unused")


def test_unsafe_schema_is_rejected_before_connecting(raw_corpus) -> None:
    directory, _, _ = raw_corpus
    with pytest.raises(ValueError, match="schema must match"):
        load_postgres(directory, "dbname=unused", "bad; DROP SCHEMA public")


def test_missing_connection_setting_is_clear(raw_corpus, monkeypatch, capsys) -> None:
    directory, _, _ = raw_corpus
    monkeypatch.delenv("DEMO_DATABASE_URL", raising=False)
    assert main(["load-postgres", str(directory)]) == 1
    assert "set DEMO_DATABASE_URL" in capsys.readouterr().err


def test_cli_database_error_does_not_print_credentials(raw_corpus, monkeypatch, capsys) -> None:
    directory, _, _ = raw_corpus
    monkeypatch.setenv("DEMO_DATABASE_URL", "postgresql://user:private-secret@invalid/db")

    def failed_load(*args, **kwargs):
        raise psycopg.OperationalError("password=private-secret connection failed")

    monkeypatch.setattr("demo_data.__main__.load_postgres", failed_load)
    assert main(["load-postgres", str(directory)]) == 1
    error = capsys.readouterr().err
    assert "PostgreSQL OperationalError" in error and "private-secret" not in error
