"""Markdown structure first, paragraph fallback only where the token budget requires it."""

import hashlib
import re
from collections.abc import Callable, Iterator

from demo_data.models import Document
from demo_data.rag_models import Chunk

TokenCounter = Callable[[str], int]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sections(text: str) -> Iterator[tuple[int, int, list[str]]]:
    start = offset = 0
    path: list[tuple[int, str]] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        fenced = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fenced:
            marker = fenced.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line) if fence is None else None
        if heading:
            if offset > start:
                yield start, offset, [name for _, name in path]
            level = len(heading.group(1))
            path = [(depth, name) for depth, name in path if depth < level]
            path.append((level, re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2))))
            start = offset
        offset += len(line)
    if offset > start:
        yield start, offset, [name for _, name in path]


def bounded_ranges(
    text: str, start: int, end: int, prefix: str, count_tokens: TokenCounter, budget: int
) -> Iterator[tuple[int, int]]:
    def fits(left: int, right: int) -> bool:
        return count_tokens(prefix + text[left:right]) <= budget

    if fits(start, end):
        yield start, end
        return
    boundaries = [start + match.end() for match in re.finditer(r"\n[ \t]*\n", text[start:end])]
    boundaries = sorted(set([*boundaries, end]))
    current = start
    pending = start
    for right in boundaries:
        if fits(current, right):
            pending = right
            continue
        if pending > current:
            yield current, pending
            current = pending
        # A paragraph can itself be oversized. Find a conservative bounded character slice;
        # counting with the real tokenizer keeps Unicode offsets exact and avoids truncation.
        while not fits(current, right):
            low, high = current + 1, right
            best = current
            while low <= high:
                middle = (low + high) // 2
                if fits(current, middle):
                    best, low = middle, middle + 1
                else:
                    high = middle - 1
            if best == current:
                raise ValueError("one source character cannot fit the embedding token budget")
            yield current, best
            current = best
        pending = right
    if pending > current:
        yield current, pending


def chunk_document(document: Document, count_tokens: TokenCounter, max_tokens: int) -> list[Chunk]:
    chunks = []
    for start, end, path in sections(document.text):
        prefix = document.title + ("\n" + " > ".join(path) if path else "") + "\n\n"
        if count_tokens(prefix) >= max_tokens:
            raise ValueError(f"section context exceeds token budget: {document.document_id}")
        for left, right in bounded_ranges(
            document.text, start, end, prefix, count_tokens, max_tokens
        ):
            text = document.text[left:right]
            embedding_input = prefix + text
            chunks.append(
                Chunk(
                    chunk_id=f"{document.document_id}:C{len(chunks):04d}",
                    document_id=document.document_id,
                    chunk_index=len(chunks),
                    section_path=path,
                    char_start=left,
                    char_end=right,
                    text=text,
                    embedding_input=embedding_input,
                    token_count=count_tokens(embedding_input),
                    source_sha256=sha256(document.text),
                    input_sha256=sha256(embedding_input),
                )
            )
    return chunks
