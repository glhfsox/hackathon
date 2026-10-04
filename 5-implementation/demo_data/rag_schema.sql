CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE IF NOT EXISTS {schema}.rag_index (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    identity JSONB NOT NULL,
    source_sha256 TEXT NOT NULL,
    documents INTEGER NOT NULL CHECK (documents > 0),
    chunks INTEGER NOT NULL CHECK (chunks > 0),
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS {schema}.document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES {schema}.documents(document_id),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    section_path TEXT[] NOT NULL,
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    text TEXT NOT NULL CHECK (length(text) = char_end - char_start),
    embedding_input TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count > 0),
    source_sha256 TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    embedding public.vector(1024) NOT NULL,
    UNIQUE (document_id, chunk_index)
);
