"""Load validated raw source files into an isolated PostgreSQL schema."""

import re
from pathlib import Path
from typing import Literal, TypedDict

import psycopg
from psycopg import sql

from demo_data.models import Corpus, Document, Model
from demo_data.storage import SOURCE_MODELS, validate_directory


class LoadResult(TypedDict):
    status: Literal["loaded", "already_loaded"]
    schema: str
    counts: dict[str, int]


def validate_schema(schema: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("schema must match [a-z][a-z0-9_]{0,62}")


def columns(model: type[Model]) -> list[str]:
    return [name for name in model.model_fields if name != "transaction_ids"]


def source_rows(corpus: Corpus, table: str, model: type[Model]) -> list[tuple[object, ...]]:
    names = columns(model)
    return [tuple(record.model_dump()[name] for name in names) for record in getattr(corpus, table)]


def document_links(documents: list[Document]) -> list[tuple[str, str, str | None]]:
    return [
        (doc.document_id, tx_id, doc.client_id)
        for doc in documents
        for tx_id in doc.transaction_ids
    ]


def verify_sources(conn: psycopg.Connection, corpus: Corpus, schema: str) -> dict[str, int]:
    counts = {}
    for table, model in SOURCE_MODELS.items():
        names = columns(model)
        statement = sql.SQL("SELECT {columns} FROM {table} ORDER BY {key}").format(
            columns=sql.SQL(", ").join(map(sql.Identifier, names)),
            table=sql.Identifier(schema, table),
            key=sql.Identifier(names[0]),
        )
        actual = conn.execute(statement).fetchall()
        expected = sorted(source_rows(corpus, table, model))
        if actual != expected:
            raise ValueError(
                f"PostgreSQL/source mismatch: {schema}.{table}; "
                "choose a new schema for a different corpus"
            )
        counts[table] = len(actual)
    links = conn.execute(
        sql.SQL(
            "SELECT document_id, transaction_id, client_id FROM {} "
            "ORDER BY document_id, transaction_id"
        ).format(sql.Identifier(schema, "document_transactions"))
    ).fetchall()
    if links != sorted(document_links(corpus.documents)):
        raise ValueError(f"PostgreSQL/source mismatch: {schema}.document_transactions")
    counts["document_transactions"] = len(links)
    return counts


def load_postgres(directory: Path, database_url: str, schema: str = "demo_data") -> LoadResult:
    validate_schema(schema)
    corpus, _ = validate_directory(directory)
    schema_sql = sql.SQL(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute(schema_sql.format(schema=sql.Identifier(schema)))
        tables = [*SOURCE_MODELS, "document_transactions"]
        # Serialize loaders so an identical concurrent load cannot create a mixed corpus.
        conn.execute(
            sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                sql.SQL(", ").join(sql.Identifier(schema, table) for table in tables)
            )
        )
        populated = any(
            conn.execute(
                sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(schema, table))
            ).fetchone()[0]
            for table in tables
        )
        if not populated:
            with conn.cursor() as cursor:
                for table, model in SOURCE_MODELS.items():
                    names = columns(model)
                    statement = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values})").format(
                        table=sql.Identifier(schema, table),
                        columns=sql.SQL(", ").join(map(sql.Identifier, names)),
                        values=sql.SQL(", ").join(sql.Placeholder() for _ in names),
                    )
                    cursor.executemany(statement, source_rows(corpus, table, model))
                cursor.executemany(
                    sql.SQL(
                        "INSERT INTO {} (document_id, transaction_id, client_id) "
                        "VALUES (%s, %s, %s)"
                    ).format(sql.Identifier(schema, "document_transactions")),
                    document_links(corpus.documents),
                )
        persisted_counts = verify_sources(conn, corpus, schema)
    return {
        "status": "already_loaded" if populated else "loaded",
        "schema": schema,
        "counts": persisted_counts,
    }
