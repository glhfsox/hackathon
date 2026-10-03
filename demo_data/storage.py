"""Portable source exports and offline corpus validation."""

import hashlib
import json
from pathlib import Path

from faker import VERSION as FAKER_VERSION

from demo_data import __version__
from demo_data.models import (
    Account,
    Client,
    Contact,
    Corpus,
    Document,
    DocumentIndex,
    GenerationConfig,
    GroundTruth,
    Manifest,
    Model,
    RetrievalQuestion,
    Transaction,
)

SOURCE_MODELS = {
    "clients": Client,
    "contacts": Contact,
    "accounts": Account,
    "transactions": Transaction,
    "documents": Document,
}


def json_line(record: Model) -> str:
    return record.model_dump_json() + "\n"


def write_jsonl(path: Path, records: list[Model]) -> None:
    path.write_text("".join(json_line(record) for record in records), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(corpus: Corpus) -> dict[str, int]:
    return {field: len(getattr(corpus, field)) for field in Corpus.model_fields}


def export(corpus: Corpus, config: GenerationConfig, output: Path) -> Manifest:
    # Revalidate at the export boundary even if a caller mutated a model after construction.
    corpus = Corpus.model_validate_json(corpus.model_dump_json())
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("records", "documents", "evaluation"):
        (output / folder).mkdir()
    for table in ("clients", "contacts", "accounts", "transactions"):
        write_jsonl(output / "records" / f"{table}.jsonl", getattr(corpus, table))
    indexes = []
    for doc in corpus.documents:
        path = f"documents/{doc.document_id}.md"
        safe_file(output, path, must_exist=False).write_text(doc.text, encoding="utf-8")
        indexes.append(DocumentIndex(**doc.model_dump(exclude={"text"}), text_path=path))
    write_jsonl(output / "documents.jsonl", indexes)
    write_jsonl(output / "evaluation" / "ground_truth.jsonl", corpus.ground_truth)
    write_jsonl(output / "evaluation" / "questions.jsonl", corpus.questions)
    files = {
        path.relative_to(output).as_posix(): digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    manifest = Manifest(
        schema_version=1,
        generator_version=__version__,
        faker_version=FAKER_VERSION,
        config=config,
        counts=counts(corpus),
        files=files,
    )
    (output / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def safe_file(root: Path, relative: str, must_exist: bool = True) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe corpus path: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"corpus path escapes root: {relative}")
    if must_exist and not resolved.is_file():
        raise ValueError(f"missing corpus file: {relative}")
    return resolved


def read_jsonl(path: Path, model: type[Model]) -> list[Model]:
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank JSONL line: {path.name}:{number}")
        try:
            result.append(model.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid {path.name}:{number}: {exc}") from exc
    return result


def validate_directory(root: Path) -> tuple[Corpus, Manifest]:
    manifest = Manifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_paths != set(manifest.files):
        raise ValueError("manifest file inventory mismatch")
    for relative, expected_digest in manifest.files.items():
        if digest(safe_file(root, relative)) != expected_digest:
            raise ValueError(f"checksum mismatch: {relative}")
    records = {
        table: read_jsonl(root / "records" / f"{table}.jsonl", model)
        for table, model in SOURCE_MODELS.items()
        if table != "documents"
    }
    docs = []
    for entry in read_jsonl(root / "documents.jsonl", DocumentIndex):
        assert isinstance(entry, DocumentIndex)
        if entry.text_path != f"documents/{entry.document_id}.md":
            raise ValueError(f"document path/id mismatch: {entry.document_id}")
        body = safe_file(root, entry.text_path).read_text(encoding="utf-8")
        payload = entry.model_dump(mode="json", exclude={"text_path"}) | {"text": body}
        docs.append(Document.model_validate_json(json.dumps(payload)))
    corpus = Corpus(
        **records,
        documents=docs,
        ground_truth=read_jsonl(root / "evaluation" / "ground_truth.jsonl", GroundTruth),
        questions=read_jsonl(root / "evaluation" / "questions.jsonl", RetrievalQuestion),
    )
    if counts(corpus) != manifest.counts:
        raise ValueError("manifest counts mismatch")
    expected_counts = {
        "clients": manifest.config.clients,
        "contacts": manifest.config.clients * 2,
        "accounts": manifest.config.clients * 2,
        "transactions": manifest.config.transactions,
        "documents": manifest.config.clients * 8 + 4,
        "ground_truth": manifest.config.clients * 8 + 4,
        "questions": manifest.config.clients * 4,
    }
    if counts(corpus) != expected_counts:
        raise ValueError("corpus/config counts mismatch")
    return corpus, manifest
