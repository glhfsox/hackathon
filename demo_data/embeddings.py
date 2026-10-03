"""Direct Python BGE-M3 dense encoding; heavyweight dependencies load only on RAG use."""

import math
from collections.abc import Sequence
from typing import Protocol

from demo_data.rag_models import RagConfig


class Encoder(Protocol):
    config: RagConfig

    def count_tokens(self, text: str) -> int: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def validate_vectors(
    vectors: Sequence[Sequence[float]], count: int, dimensions: int
) -> list[list[float]]:
    if len(vectors) != count:
        raise ValueError("embedding response count does not match inputs")
    output = []
    for vector in vectors:
        if len(vector) != dimensions:
            raise ValueError("embedding dimensions do not match the index")
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding contains nonfinite values")
        if not any(values):
            raise ValueError("embedding has zero length")
        output.append(values)
    return output


class BgeM3Encoder:
    def __init__(self, config: RagConfig):
        from httpx import HTTPError
        from huggingface_hub import constants, snapshot_download
        from sentence_transformers import SentenceTransformer

        self.config = config
        # Load a pinned local snapshot so Transformers cannot start a background remote
        # safetensors conversion/download from an unrelated PR revision.
        try:
            snapshot = snapshot_download(
                config.model_name,
                revision=config.model_revision,
                local_files_only=constants.HF_HUB_OFFLINE,
                allow_patterns=[
                    "config*.json",
                    "modules.json",
                    "sentence_bert_config.json",
                    "tokenizer*.json",
                    "special_tokens_map.json",
                    "sentencepiece.bpe.model",
                    "pytorch_model.bin",
                    "1_Pooling/config.json",
                ],
            )
            self.model = SentenceTransformer(
                snapshot,
                device=config.device,
                trust_remote_code=False,
                local_files_only=True,
                model_kwargs={"use_safetensors": False},
            )
        except (OSError, RuntimeError, HTTPError) as exc:
            raise ValueError(
                f"BGE-M3 model unavailable ({type(exc).__name__}); check cache, network, and device"
            ) from exc
        if self.model.get_embedding_dimension() != config.dimensions:
            raise ValueError("loaded BGE-M3 dimensions differ from configured identity")
        self.max_tokens = min(int(self.model.max_seq_length), 8192)
        if self.max_tokens < config.max_chunk_tokens:
            raise ValueError("chunk token budget exceeds the loaded model limit")

    def count_tokens(self, text: str) -> int:
        return len(self.model.tokenizer.encode(text, add_special_tokens=True, truncation=False))

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(self.count_tokens(text) > self.max_tokens for text in texts):
            raise ValueError("embedding input exceeds model limit; refusing silent truncation")
        vectors = self.model.encode(
            texts,
            batch_size=self.config.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return validate_vectors(vectors, len(texts), self.config.dimensions)
