"""Small atomic index and read-only treasury tools over the existing PostgreSQL sources."""

import json
import re
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from demo_data.chunking import chunk_document, sha256
from demo_data.embeddings import BgeM3Encoder, Encoder, validate_vectors
from demo_data.models import Document
from demo_data.postgres import validate_schema
from demo_data.rag_models import Chunk, RagConfig, SearchArgs, SearchHit, TransactionArgs


def source_documents(conn: psycopg.Connection, schema: str) -> list[Document]:
    rows = conn.execute(
        sql.SQL(
            "SELECT d.*, ARRAY(SELECT l.transaction_id FROM {links} l "
            "WHERE l.document_id = d.document_id ORDER BY l.transaction_id) AS transaction_ids "
            "FROM {documents} d ORDER BY d.document_id"
        ).format(
            links=sql.Identifier(schema, "document_transactions"),
            documents=sql.Identifier(schema, "documents"),
        )
    ).fetchall()
    return [Document.model_validate(row) for row in rows]


def source_fingerprint(documents: list[Document]) -> str:
    return sha256(
        json.dumps(
            [doc.model_dump(mode="json") for doc in documents], sort_keys=True, ensure_ascii=False
        )
    )


def index_state(conn: psycopg.Connection, schema: str) -> dict | None:
    exists = conn.execute("SELECT to_regclass(%s) AS name", (schema + ".rag_index",)).fetchone()
    if exists["name"] is None:
        return None
    return conn.execute(
        sql.SQL("SELECT * FROM {} WHERE id = 1").format(sql.Identifier(schema, "rag_index"))
    ).fetchone()


def matching_index(
    conn: psycopg.Connection,
    schema: str,
    state: dict | None,
    config: RagConfig,
    fingerprint: str,
) -> bool:
    if not state or state["identity"] != config.identity() or state["source_sha256"] != fingerprint:
        return False
    count = conn.execute(
        sql.SQL("SELECT count(*) AS count FROM {}").format(
            sql.Identifier(schema, "document_chunks")
        )
    ).fetchone()["count"]
    return count == state["chunks"]


def index_documents(
    database_url: str,
    config: RagConfig,
    schema: str = "demo_data",
    *,
    rebuild: bool = False,
    encoder: Encoder | None = None,
) -> dict:
    validate_schema(schema)
    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        documents = source_documents(conn, schema)
        if not documents:
            raise ValueError("no source documents; run load-postgres first")
        fingerprint = source_fingerprint(documents)
        state = index_state(conn, schema)
        if matching_index(conn, schema, state, config, fingerprint):
            return {
                "status": "already_indexed",
                "documents": state["documents"],
                "chunks": state["chunks"],
            }
        if state and not rebuild:
            raise ValueError("index identity/source mismatch; run index --rebuild")

    encoder = encoder or BgeM3Encoder(config)
    if encoder.config.identity() != config.identity():
        raise ValueError("encoder identity does not match index configuration")
    chunks = [
        chunk
        for doc in documents
        for chunk in chunk_document(doc, encoder.count_tokens, config.max_chunk_tokens)
    ]
    vectors = validate_vectors(
        encoder.encode([chunk.embedding_input for chunk in chunks]), len(chunks), config.dimensions
    )
    if not chunks:
        raise ValueError("source documents contain no indexable text")
    from pgvector import Vector
    from pgvector.psycopg import register_vector

    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as conn:
        # Serialize index writers and freeze raw document/link writes only for the final commit.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (schema + ":rag",))
        conn.execute(
            sql.SQL("LOCK TABLE {}, {} IN SHARE MODE").format(
                sql.Identifier(schema, "documents"), sql.Identifier(schema, "document_transactions")
            )
        )
        if source_fingerprint(source_documents(conn, schema)) != fingerprint:
            raise ValueError("source documents changed while embedding; retry indexing")
        state = index_state(conn, schema)
        if matching_index(conn, schema, state, config, fingerprint):
            return {
                "status": "already_indexed",
                "documents": state["documents"],
                "chunks": state["chunks"],
            }
        if state and not rebuild:
            raise ValueError("concurrent index differs; run index --rebuild")
        ddl = sql.SQL(Path(__file__).with_name("rag_schema.sql").read_text(encoding="utf-8"))
        conn.execute(ddl.format(schema=sql.Identifier(schema)))
        register_vector(conn)
        conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(schema, "document_chunks")))
        names = list(Chunk.model_fields)
        statement = sql.SQL("INSERT INTO {} ({}, embedding) VALUES ({}, %s)").format(
            sql.Identifier(schema, "document_chunks"),
            sql.SQL(", ").join(map(sql.Identifier, names)),
            sql.SQL(", ").join(sql.Placeholder() for _ in names),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                statement,
                [
                    (*[chunk.model_dump()[name] for name in names], Vector(vector))
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
        conn.execute(
            sql.SQL(
                "INSERT INTO {} (id, identity, source_sha256, documents, chunks) "
                "VALUES (1,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET identity = EXCLUDED.identity, "
                "source_sha256 = EXCLUDED.source_sha256, documents = EXCLUDED.documents, "
                "chunks = EXCLUDED.chunks, indexed_at = CURRENT_TIMESTAMP"
            ).format(sql.Identifier(schema, "rag_index")),
            (Jsonb(config.identity()), fingerprint, len(documents), len(chunks)),
        )
    return {"status": "indexed", "documents": len(documents), "chunks": len(chunks)}


class TreasuryTools:
    def __init__(
        self,
        database_url: str,
        client_id: str,
        config: RagConfig,
        schema: str = "demo_data",
        *,
        encoder: Encoder | None = None,
    ):
        validate_schema(schema)
        if not re.fullmatch(r"CLI-[0-9]{4}", client_id):
            raise ValueError("client_id must match CLI-[0-9]{4}")
        self.database_url, self.client_id = database_url, client_id
        self.config, self.schema, self.encoder = config, schema, encoder
        with self.connection() as conn:
            exists = conn.execute(
                sql.SQL("SELECT 1 FROM {} WHERE client_id = %s").format(
                    sql.Identifier(schema, "clients")
                ),
                (client_id,),
            ).fetchone()
            if not exists:
                raise ValueError("unknown client in demo source database")

    def connection(self) -> psycopg.Connection:
        conn = psycopg.connect(self.database_url, connect_timeout=5, row_factory=dict_row)
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return conn

    def search_documents(self, args: SearchArgs) -> dict:
        from pgvector import Vector
        from pgvector.psycopg import register_vector

        with self.connection() as conn:
            state = index_state(conn, self.schema)
            if not matching_index(
                conn,
                self.schema,
                state,
                self.config,
                source_fingerprint(source_documents(conn, self.schema)),
            ):
                raise ValueError("missing/stale index; run index (or index --rebuild)")
            self.encoder = self.encoder or BgeM3Encoder(self.config)
            if self.encoder.config.identity() != self.config.identity():
                raise ValueError("query encoder identity differs from index")
            vector = Vector(
                validate_vectors(self.encoder.encode([args.query]), 1, self.config.dimensions)[0]
            )
            register_vector(conn)
            rows = conn.execute(
                sql.SQL(
                    "SELECT c.chunk_id,c.document_id,d.client_id,d.title,d.classification,"
                    "c.section_path,c.text,c.char_start,c.char_end,"
                    "1 - (c.embedding <=> %s) AS similarity FROM {chunks} c "
                    "JOIN {documents} d ON d.document_id = c.document_id "
                    "WHERE (d.client_id = %s OR "
                    "(d.client_id IS NULL AND d.classification = 'public')) "
                    "AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM {links} l "
                    "WHERE l.document_id = d.document_id AND l.transaction_id = %s)) "
                    "ORDER BY c.embedding <=> %s, c.chunk_id LIMIT %s"
                ).format(
                    chunks=sql.Identifier(self.schema, "document_chunks"),
                    documents=sql.Identifier(self.schema, "documents"),
                    links=sql.Identifier(self.schema, "document_transactions"),
                ),
                (vector, self.client_id, args.transaction_id, args.transaction_id, vector, args.k),
            ).fetchall()
        return {"chunks": [SearchHit.model_validate(row).model_dump(mode="json") for row in rows]}

    def query_transactions(self, args: TransactionArgs) -> dict:
        filters = sql.SQL(
            "client_id = %s AND (%s::text IS NULL OR transaction_id = %s) "
            "AND (%s::text IS NULL OR status = %s)"
        )
        params = (
            self.client_id,
            args.transaction_id,
            args.transaction_id,
            args.status,
            args.status,
        )
        table = sql.Identifier(self.schema, "transactions")
        with self.connection() as conn:
            rows = conn.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE {} ORDER BY booked_at,transaction_id LIMIT %s"
                ).format(table, filters),
                (*params, args.limit),
            ).fetchall()
            totals = conn.execute(
                sql.SQL(
                    "SELECT currency, count(*)::bigint AS count, "
                    "coalesce(sum(amount_minor) FILTER (WHERE direction='credit'),0)"
                    "::bigint AS credit_minor, "
                    "coalesce(sum(amount_minor) FILTER (WHERE direction='debit'),0)"
                    "::bigint AS debit_minor "
                    "FROM {} WHERE {} GROUP BY currency ORDER BY currency"
                ).format(table, filters),
                params,
            ).fetchall()
        from demo_data.models import Transaction

        return {
            "transactions": [
                Transaction.model_validate(row).model_dump(mode="json") for row in rows
            ],
            "matching_count": sum(total["count"] for total in totals),
            "totals_by_currency": totals,
        }

    def run(self, name: str, arguments: dict) -> dict:
        if name == "search_documents":
            return self.search_documents(SearchArgs.model_validate(arguments))
        if name == "query_transactions":
            return self.query_transactions(TransactionArgs.model_validate(arguments))
        raise ValueError("unknown treasury tool")
