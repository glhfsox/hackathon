import math
import sys
from types import SimpleNamespace

import pytest
from demo_data.embeddings import BgeM3Encoder, validate_vectors
from demo_data.rag_models import RagConfig


@pytest.mark.parametrize(
    "vectors,count,dimensions",
    [
        ([[1.0, 0.0]], 2, 2),
        ([[1.0]], 1, 2),
        ([[math.nan, 0.0]], 1, 2),
        ([[math.inf, 0.0]], 1, 2),
        ([[0.0, 0.0]], 1, 2),
    ],
)
def test_reject_invalid_model_vectors(vectors, count, dimensions) -> None:
    with pytest.raises(ValueError):
        validate_vectors(vectors, count, dimensions)


def test_valid_vectors_are_plain_finite_numbers() -> None:
    assert validate_vectors([[1, 0], [0, 1]], 2, 2) == [[1.0, 0.0], [0.0, 1.0]]


def test_bge_encoder_pins_revision_and_refuses_truncation_before_inference(monkeypatch) -> None:
    seen = {}

    class FakeModel:
        max_seq_length = 8192
        tokenizer = SimpleNamespace(encode=lambda text, **kwargs: [0] * (len(text) + 2))

        def __init__(self, name, **kwargs):
            seen.update(name=name, **kwargs)

        def get_embedding_dimension(self):
            return 1024

        def encode(self, texts, **kwargs):
            seen["encode"] = kwargs
            return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeModel)
    )

    def snapshot(name, **kwargs):
        seen.update(snapshot_name=name, snapshot_revision=kwargs["revision"])
        return "/pinned/local/model"

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot, constants=SimpleNamespace(HF_HUB_OFFLINE=True)),
    )
    encoder = BgeM3Encoder(RagConfig())
    assert seen["snapshot_revision"] == encoder.config.model_revision
    assert seen["name"] == "/pinned/local/model" and seen["local_files_only"] is True
    assert seen["trust_remote_code"] is False
    assert len(encoder.encode(["Łódź"])[0]) == 1024
    assert seen["encode"]["normalize_embeddings"] is True
    seen.pop("encode")
    with pytest.raises(ValueError, match="refusing silent truncation"):
        encoder.encode(["x" * 8192])
    assert "encode" not in seen


def test_model_download_failure_has_a_safe_explicit_error(monkeypatch) -> None:
    def failed_download(*args, **kwargs):
        raise OSError("provider error with token=private-secret")

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=None)
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            snapshot_download=failed_download, constants=SimpleNamespace(HF_HUB_OFFLINE=False)
        ),
    )
    with pytest.raises(ValueError, match="BGE-M3 model unavailable") as caught:
        BgeM3Encoder(RagConfig())
    assert "private-secret" not in str(caught.value)
