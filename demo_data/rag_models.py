"""Demo RAG shapes mirror contracts/rag-demo.md; no middleware policy lives here."""

from typing import Literal

from pydantic import Field

from demo_data.models import Classification, Model


class RagConfig(Model):
    model_name: Literal["BAAI/bge-m3"] = "BAAI/bge-m3"
    model_revision: str = Field(
        default="5617a9f61b028005a4858fdac845db406aefb181", pattern=r"^[a-f0-9]{40}$"
    )
    dimensions: Literal[1024] = 1024
    max_chunk_tokens: int = Field(default=350, ge=64, le=2048)
    batch_size: int = Field(default=8, ge=1, le=64)
    device: str = Field(default="cpu", min_length=1)

    def identity(self) -> dict[str, str | int]:
        return {
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
            "max_chunk_tokens": self.max_chunk_tokens,
            "chunker": "markdown-structure-v1",
            "pooling": "cls-normalized",
        }


class Chunk(Model):
    chunk_id: str
    document_id: str
    chunk_index: int = Field(ge=0)
    section_path: list[str]
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text: str = Field(min_length=1)
    embedding_input: str = Field(min_length=1)
    token_count: int = Field(gt=0)
    source_sha256: str
    input_sha256: str


class SearchArgs(Model):
    query: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    k: int = Field(default=5, ge=1, le=10)
    transaction_id: str | None = Field(default=None, pattern=r"^TXN-[0-9]{6}$")


class TransactionArgs(Model):
    transaction_id: str | None = Field(default=None, pattern=r"^TXN-[0-9]{6}$")
    status: Literal["settled", "pending", "held"] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class SearchHit(Model):
    chunk_id: str
    document_id: str
    client_id: str | None
    title: str
    classification: Classification
    section_path: list[str]
    text: str
    char_start: int
    char_end: int
    similarity: float
