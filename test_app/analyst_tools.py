"""Agent A's read-only tools over the generated corpus files, with no database or embeddings.

Arguments and result shapes match demo_data.rag.TreasuryTools (contracts/rag-demo.md), so the
Analyst can switch to the PostgreSQL/BGE-M3 tools without changes. Search here is plain keyword
overlap per Markdown section: good enough to retrieve the demo documents, not a relevance claim.
"""

import math
import re
from collections import defaultdict
from pathlib import Path

from demo_data.models import Corpus
from demo_data.rag_models import SearchArgs, TransactionArgs
from demo_data.storage import validate_directory

WORD = re.compile(r"[\w-]+")


def words(text: str) -> set[str]:
    return {w.casefold() for w in WORD.findall(text) if len(w) > 2}


def sections(text: str) -> list[tuple[list[str], int, int]]:
    """(heading path, start, end) per `## ` section; the title block is its own section."""
    starts = [m.start() for m in re.finditer(r"(?m)^## ", text)]
    bounds = [0, *starts, len(text)]
    title = text.splitlines()[0].lstrip("# ").strip() if text else ""
    result = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        if not text[start:end].strip():
            continue
        heading = text[start:end].splitlines()[0]
        path = [title] if start == 0 else [title, heading.removeprefix("## ").strip()]
        result.append((path, start, end))
    return result


class CorpusTools:
    """search_documents and query_transactions for one operator-bound client."""

    def __init__(self, corpus_dir: Path, client_id: str):
        corpus, _ = validate_directory(corpus_dir)
        if client_id not in {c.client_id for c in corpus.clients}:
            raise ValueError(f"unknown client {client_id!r} in {corpus_dir}")
        self.corpus: Corpus = corpus
        self.client_id = client_id

    def search_documents(self, args: SearchArgs) -> dict:
        query = words(args.query)
        hits = []
        for doc in self.corpus.documents:
            # Same scope as the PostgreSQL search: this client plus global public guides.
            in_scope = doc.client_id == self.client_id or (
                doc.client_id is None and doc.classification == "public"
            )
            if not in_scope or (
                args.transaction_id and args.transaction_id not in doc.transaction_ids
            ):
                continue
            for index, (path, start, end) in enumerate(sections(doc.text)):
                body = words(doc.text[start:end]) | words(doc.title)
                overlap = len(query & body)
                if not overlap:
                    continue
                hits.append(
                    {
                        "chunk_id": f"{doc.document_id}-{index:03d}",
                        "document_id": doc.document_id,
                        "client_id": doc.client_id,
                        "title": doc.title,
                        "classification": doc.classification,
                        "section_path": path,
                        "text": doc.text[start:end],
                        "char_start": start,
                        "char_end": end,
                        "similarity": round(overlap / math.sqrt(len(query) * len(body)), 4),
                    }
                )
        hits.sort(key=lambda hit: (-hit["similarity"], hit["chunk_id"]))
        return {"chunks": hits[: args.k]}

    def query_transactions(self, args: TransactionArgs) -> dict:
        rows = sorted(
            (
                t
                for t in self.corpus.transactions
                if t.client_id == self.client_id
                and args.transaction_id in (None, t.transaction_id)
                and args.status in (None, t.status)
            ),
            key=lambda t: (t.booked_at, t.transaction_id),
        )
        totals: dict[str, dict] = defaultdict(
            lambda: {"count": 0, "credit_minor": 0, "debit_minor": 0}
        )
        for t in rows:
            total = totals[t.currency]
            total["count"] += 1
            total[f"{t.direction}_minor"] += t.amount_minor
        return {
            "transactions": [t.model_dump(mode="json") for t in rows[: args.limit]],
            "matching_count": len(rows),
            "totals_by_currency": [
                {"currency": currency, **totals[currency]} for currency in sorted(totals)
            ],
        }

    def run(self, name: str, arguments: dict) -> dict:
        if name == "search_documents":
            return self.search_documents(SearchArgs.model_validate(arguments))
        if name == "query_transactions":
            return self.query_transactions(TransactionArgs.model_validate(arguments))
        raise ValueError("unknown analyst tool")
