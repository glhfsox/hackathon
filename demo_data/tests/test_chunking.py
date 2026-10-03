from datetime import UTC, datetime

import pytest
from demo_data.chunking import chunk_document
from demo_data.models import Document


def document(text: str) -> Document:
    return Document(
        document_id="DOC-00001",
        client_id="CLI-0001",
        title="Treasury — Różak",
        document_type="investigation",
        classification="confidential",
        source="internal_memo",
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
        transaction_ids=[],
        text=text,
    )


def count_chars(text: str) -> int:
    # A deliberately strict budget makes fallback/offset errors visible without model downloads.
    return len(text) + 2


def test_headings_unicode_and_fences_preserve_exact_source() -> None:
    text = (
        "# Main\n\nŁódź treasury.\n\n## Payments\n\nWhy held?\n\n"
        "```markdown\n# Fake heading\n```\n\n### Notes\n\nżółć\n"
    )
    chunks = chunk_document(document(text), count_chars, 300)
    assert "".join(chunk.text for chunk in chunks) == text
    assert [c.section_path for c in chunks] == [
        ["Main"],
        ["Main", "Payments"],
        ["Main", "Payments", "Notes"],
    ]
    assert "# Fake heading" in chunks[1].text
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.token_count == count_chars(chunk.embedding_input) <= 300


def test_oversized_sections_prefer_paragraphs_and_bound_single_paragraph() -> None:
    text = "# Main\n\n" + "a" * 30 + "\n\n" + "ą" * 400 + "\n\nFinal paragraph.\n"
    chunks = chunk_document(document(text), count_chars, 100)
    assert len(chunks) > 5
    assert "".join(c.text for c in chunks) == text
    assert any(c.text.endswith("\n\n") for c in chunks)
    assert all(c.token_count <= 100 for c in chunks)
    assert all(c.section_path == ["Main"] for c in chunks)
    assert chunks[-1].char_end == len(text)
    assert chunks == chunk_document(document(text), count_chars, 100)


def test_empty_text_and_oversized_heading_have_explicit_behavior() -> None:
    assert chunk_document(document(""), count_chars, 100) == []
    with pytest.raises(ValueError, match="section context exceeds"):
        chunk_document(document("# " + "x" * 200), count_chars, 100)
