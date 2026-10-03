import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from demo_data.__main__ import main
from demo_data.generate import generate
from demo_data.models import Corpus, GenerationConfig
from demo_data.storage import export, validate_directory
from pydantic import ValidationError


@pytest.fixture
def config() -> GenerationConfig:
    return GenerationConfig(
        schema_version=1,
        seed=20261003,
        clients=4,
        transactions=32,
        reference_date=date(2026, 10, 3),
    )


@pytest.fixture
def corpus(config: GenerationConfig) -> Corpus:
    return generate(config)


def test_repeatable_and_seed_changes_sources(config: GenerationConfig, corpus: Corpus) -> None:
    assert generate(config).model_dump_json() == corpus.model_dump_json()
    other = GenerationConfig.model_validate_json(
        json.dumps(config.model_dump(mode="json") | {"seed": config.seed + 1})
    )
    assert generate(other).clients != corpus.clients
    assert generate(other).transactions != corpus.transactions


def test_document_payment_facts_and_liquidity(corpus: Corpus) -> None:
    for client in corpus.clients:
        tx = next(t for t in corpus.transactions if t.client_id == client.client_id)
        doc = next(
            d
            for d in corpus.documents
            if d.client_id == client.client_id and d.document_type == "payment_investigation"
        )
        assert doc.transaction_ids == [tx.transaction_id]
        assert tx.reference in doc.text and f"Current status: {tx.status}" in doc.text
        assert f"{tx.amount_minor // 100:,}.{tx.amount_minor % 100:02d} {tx.currency}" in doc.text
        account = next(a for a in corpus.accounts if a.client_id == client.client_id)
        settled_net = sum(
            t.amount_minor if t.direction == "credit" else -t.amount_minor
            for t in corpus.transactions
            if t.account_id == account.account_id and t.status == "settled"
        )
        question = next(
            q
            for q in corpus.questions
            if q.client_id == client.client_id and "liquidity_minor" in q.answer_facts
        )
        assert int(question.answer_facts["liquidity_minor"]) == (
            account.opening_balance_minor + settled_net
        )


def test_identifiers_have_valid_checksums(corpus: Corpus) -> None:
    for account in corpus.accounts:
        rearranged = account.iban[4:] + account.iban[:4]
        numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
        assert int(numeric) % 97 == 1
    for contact in corpus.contacts:
        if contact.identifier_kind == "PESEL":
            digits = [int(c) for c in contact.identifier]
            weighted = sum(a * b for a, b in zip(digits[:10], [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]))
            assert len(digits) == 11 and (-weighted) % 10 == digits[-1]
        else:
            first, second, third = contact.identifier.split("-")
            assert 0 < int(first) < 900 and int(first) != 666
            assert int(second) > 0 and int(third) > 0


def test_security_coverage_and_pair_metadata(corpus: Corpus) -> None:
    docs = {d.document_id: d for d in corpus.documents}
    assert {g.attack_family for g in corpus.ground_truth if g.attack_family} == {
        "instruction_override",
        "authority_spoofing",
        "data_exfiltration",
        "malicious_tool_instruction",
    }
    kinds = {s.kind for g in corpus.ground_truth for s in g.pii_spans}
    assert kinds == {"EMAIL", "PHONE", "PESEL", "SSN", "IBAN"}
    for poisoned in (g for g in corpus.ground_truth if g.variant == "poisoned"):
        clean = next(
            g for g in corpus.ground_truth if g.pair_id == poisoned.pair_id and g.variant == "clean"
        )
        assert docs[clean.document_id].model_dump(exclude={"document_id", "text"}) == (
            docs[poisoned.document_id].model_dump(exclude={"document_id", "text"})
        )
        assert docs[clean.document_id].text.rstrip() in docs[poisoned.document_id].text
        assert not clean.attack_spans
        assert any(
            q.relevant_document_ids == [clean.document_id, poisoned.document_id]
            for q in corpus.questions
        )
    training = next(d for d in corpus.documents if "Security training" in d.title)
    assert "ignore previous instructions" in training.text
    assert not next(
        g for g in corpus.ground_truth if g.document_id == training.document_id
    ).attack_spans


def test_export_roundtrip_and_no_label_leakage(
    tmp_path: Path,
    config: GenerationConfig,
    corpus: Corpus,
) -> None:
    root = tmp_path / "corpus"
    export(corpus, config, root)
    loaded, manifest = validate_directory(root)
    assert loaded == corpus
    assert manifest.counts["documents"] == 36
    source_metadata = (root / "documents.jsonl").read_text()
    for forbidden in ("attack_family", "attack_spans", "pair_id", "variant", "answer_facts"):
        assert forbidden not in source_metadata
    with sqlite3.connect(root / "corpus.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone() == (32,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute(
            "SELECT COUNT(*) FROM documents WHERE text LIKE '%Processing note%'"
        ).fetchone() == (4,)


def test_export_bytes_are_repeatable(
    tmp_path: Path, config: GenerationConfig, corpus: Corpus
) -> None:
    first = export(corpus, config, tmp_path / "first")
    second = export(generate(config), config, tmp_path / "second")
    assert first.files == second.files
    assert (tmp_path / "first" / "manifest.json").read_bytes() == (
        tmp_path / "second" / "manifest.json"
    ).read_bytes()


def test_rejects_overwrite(tmp_path: Path, config: GenerationConfig, corpus: Corpus) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        export(corpus, config, root)
    assert sentinel.read_text() == "keep" and list(root.iterdir()) == [sentinel]


@pytest.mark.parametrize(
    "mutation",
    [
        {"clients": 3},
        {"clients": 40},
        {"seed": "42"},
        {"locale": "fr_FR"},
    ],
)
def test_invalid_config(config: GenerationConfig, mutation: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GenerationConfig.model_validate_json(json.dumps(config.model_dump(mode="json") | mutation))


@pytest.mark.parametrize("mutation", ["span", "client", "currency", "question", "pair"])
def test_invalid_corpus_references(corpus: Corpus, mutation: str) -> None:
    payload = corpus.model_dump(mode="json")
    if mutation == "span":
        payload["ground_truth"][0]["pii_spans"][0]["start"] += 1
        payload["ground_truth"][0]["pii_spans"][0]["end"] += 1
    elif mutation == "client":
        payload["contacts"][0]["client_id"] = "missing"
    elif mutation == "currency":
        payload["transactions"][0]["currency"] = "USD"
        payload["accounts"][0]["currency"] = "PLN"
        payload["accounts"][1]["currency"] = "PLN"
    elif mutation == "question":
        payload["questions"][0]["relevant_document_ids"] = ["missing"]
    else:
        item = next(g for g in payload["ground_truth"] if g["variant"] == "clean")
        item["pair_id"] = "missing-pair"
    with pytest.raises(ValidationError):
        Corpus.model_validate_json(json.dumps(payload))


def test_modified_source_is_detected(
    tmp_path: Path, config: GenerationConfig, corpus: Corpus
) -> None:
    root = tmp_path / "corpus"
    export(corpus, config, root)
    path = root / "documents" / f"{corpus.documents[0].document_id}.md"
    path.write_text(path.read_text() + "modified")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_directory(root)


def test_snapshot_disagreement_is_detected(
    tmp_path: Path,
    config: GenerationConfig,
    corpus: Corpus,
) -> None:
    root = tmp_path / "corpus"
    export(corpus, config, root)
    path = root / "corpus.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE clients SET name = 'changed'")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["corpus.sqlite3"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="SQLite/export mismatch"):
        validate_directory(root)


def test_cli_runs_and_reports_invalid_input(
    tmp_path: Path, config: GenerationConfig, capsys
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(config.model_dump_json())
    root = tmp_path / "corpus"
    assert main(["generate", "--config", str(config_path), "--output", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "generated"
    assert main(["validate", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    assert main(["generate", "--config", str(config_path), "--output", str(root)]) == 1
    assert "output already exists" in capsys.readouterr().err
    assert main(["validate", str(tmp_path / "missing")]) == 1
    assert "validate failed" in capsys.readouterr().err
