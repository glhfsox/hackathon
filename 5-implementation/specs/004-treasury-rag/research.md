# Research decisions

## Chunking

**Decision**: Markdown headings and paragraphs; 350-token total embedding-input budget; bounded character subdivision only when one paragraph exceeds that budget. No overlap initially.
**Rationale**: These fixtures already have short named sections. Preserve the source exactly and keep original offsets. Overlap, recursive framework splitters, semantic chunking, and rerankers would add knobs without a demonstrated retrieval problem.
**Alternatives**: General recursive chunking is useful for future unstructured/longer sources but is not required here.

## Embeddings

**Decision**: Direct `sentence-transformers` dense BGE-M3 using revision `5617a9f61b028005a4858fdac845db406aefb181`, 1024 dimensions, CLS pooling supplied by the model, normalized vectors, CPU default.
**Rationale**: Multilingual text and Polish diacritics; same document/query encoder; revision and input hashes support repeatability. BGE-M3 needs no query instruction prefix. The model supports 8192 tokens; the demo budget stays much smaller. Reject oversized input before encode rather than rely on library truncation.
**Sources**: [BAAI card](https://huggingface.co/BAAI/bge-m3), [revision API](https://huggingface.co/api/models/BAAI/bge-m3), [pooling](https://huggingface.co/BAAI/bge-m3/blob/main/1_Pooling/config.json), [SentenceTransformer API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html).
**Alternatives**: FlagEmbedding includes sparse/multivector functionality unused here; Ollama embeddings explicitly rejected by the user.

**Runtime finding**: Load the pinned snapshot from local files after download. This prevents the installed Transformers version from starting a background safetensors conversion/download from an unrelated model pull-request revision. The model's original weights and declared pooling configuration are used.

## Storage

**Decision**: PostgreSQL 16 with `pgvector/pgvector:0.8.7-pg16-trixie`, retaining the named data volume. Enable vector explicitly and use exact cosine search through `pgvector.psycopg`.
**Rationale**: Same database, major version, and Debian generation as the existing volume. A bookworm smoke run exposed a libc collation-version mismatch; trixie avoids changing collation under the persisted data. Populated volumes do not rerun init scripts. Hundreds of chunks need no approximate index.
**Sources**: [pgvector](https://github.com/pgvector/pgvector), [Python adapter](https://github.com/pgvector/pgvector-python), [PostgreSQL image](https://hub.docker.com/_/postgres).

## Agent integration

**Decision**: Small HTTP loop against existing contracts. Operator-scoped client, two read-only tools, configurable chat model; no direct upstream fallback. A separate checkout/copy of `origin/feature/proxy` proves middleware compatibility without changing backend files.
**Rationale**: Working middleware exists remotely; this feature does not need to own it. Scripted upstream is deterministic test infrastructure, not a substitute for middleware. Tool-guard redactions have no replacement-argument field, so an unappliable redaction stops execution.
**Sources**: Existing repository contracts and remote backend endpoint/schema implementation inspected after fetching.

## Security evaluation limits

**Decision**: Index source text faithfully, including attacks and confidential spans; evaluate labels only after retrieval. Search may return both clean and poisoned fixtures; an attack block is meaningful only when the poisoned text was actually retrieved.
**Rationale**: The user is testing the middleware. Do not pre-clean inputs or quietly turn classification into a new security policy.
