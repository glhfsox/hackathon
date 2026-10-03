"""Models mirror contracts/demo-data.md; annotations never enter retrieval sources."""

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Currency = Literal["PLN", "EUR", "USD"]
Classification = Literal["public", "internal", "confidential", "restricted"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GenerationConfig(Model):
    schema_version: Literal[1]
    seed: int
    clients: int = Field(ge=4)
    transactions: int = Field(ge=4)
    reference_date: date

    @model_validator(mode="after")
    def enough_transactions(self) -> Self:
        if self.transactions < self.clients:
            raise ValueError("transactions must be at least clients")
        return self


class Client(Model):
    client_id: str
    name: str
    country: Literal["PL", "US"]
    sector: str


class Contact(Model):
    contact_id: str
    client_id: str
    name: str
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.example$")
    phone: str
    identifier_kind: Literal["PESEL", "SSN"]
    identifier: str


class Account(Model):
    account_id: str
    client_id: str
    iban: str
    currency: Currency
    opening_balance_minor: int = Field(ge=0)


class Transaction(Model):
    transaction_id: str
    account_id: str
    client_id: str
    counterparty: str
    amount_minor: int = Field(gt=0)
    currency: Currency
    direction: Literal["credit", "debit"]
    status: Literal["settled", "pending", "held"]
    booked_at: datetime
    reference: str


class Document(Model):
    document_id: str
    client_id: str | None
    title: str
    document_type: str
    classification: Classification
    source: Literal["internal_memo", "client_record", "supplier_email", "public_guide"]
    created_at: datetime
    transaction_ids: list[str]
    text: str


class DocumentIndex(Model):
    document_id: str
    client_id: str | None
    title: str
    document_type: str
    classification: Classification
    source: Literal["internal_memo", "client_record", "supplier_email", "public_guide"]
    created_at: datetime
    transaction_ids: list[str]
    text_path: str


class Span(Model):
    kind: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    value: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.value):
            raise ValueError("span range must match value length")
        return self


class GroundTruth(Model):
    document_id: str
    pii_spans: list[Span]
    confidential_spans: list[Span]
    attack_spans: list[Span]
    attack_family: str | None
    pair_id: str | None
    variant: Literal["clean", "poisoned"] | None

    @model_validator(mode="after")
    def valid_attack(self) -> Self:
        if self.variant == "poisoned":
            if not self.pair_id or not self.attack_family or not self.attack_spans:
                raise ValueError("poisoned variant requires pair, family, and attack spans")
        elif self.attack_spans or self.attack_family:
            raise ValueError("only poisoned variants may have attack annotations")
        if self.variant == "clean" and not self.pair_id:
            raise ValueError("clean variant requires pair_id")
        if self.variant is None and self.pair_id is not None:
            raise ValueError("pair_id requires a variant")
        return self


class RetrievalQuestion(Model):
    question_id: str
    client_id: str
    query: str
    relevant_document_ids: list[str] = Field(min_length=1)
    relevant_transaction_ids: list[str]
    answer_facts: dict[str, str]


class Manifest(Model):
    schema_version: Literal[1]
    generator_version: str
    faker_version: str
    config: GenerationConfig
    counts: dict[str, int]
    files: dict[str, str]


class Corpus(Model):
    clients: list[Client]
    contacts: list[Contact]
    accounts: list[Account]
    transactions: list[Transaction]
    documents: list[Document]
    ground_truth: list[GroundTruth]
    questions: list[RetrievalQuestion]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        def by_id(records: list[Model], key: str) -> dict[str, Model]:
            result = {getattr(record, key): record for record in records}
            if len(result) != len(records):
                raise ValueError(f"duplicate {key}")
            return result

        clients = by_id(self.clients, "client_id")
        by_id(self.contacts, "contact_id")
        accounts = by_id(self.accounts, "account_id")
        transactions = by_id(self.transactions, "transaction_id")
        documents = by_id(self.documents, "document_id")
        truth = by_id(self.ground_truth, "document_id")
        by_id(self.questions, "question_id")
        if documents.keys() != truth.keys():
            raise ValueError("every document requires exactly one ground-truth record")
        for item in [*self.contacts, *self.accounts, *self.transactions]:
            if item.client_id not in clients:
                raise ValueError(f"unknown client: {item.client_id}")
        for tx in self.transactions:
            account = accounts.get(tx.account_id)
            if not isinstance(account, Account):
                raise ValueError(f"unknown account: {tx.account_id}")
            if (tx.client_id, tx.currency) != (account.client_id, account.currency):
                raise ValueError(f"account/client/currency mismatch: {tx.transaction_id}")
        for doc in self.documents:
            if doc.client_id is not None and doc.client_id not in clients:
                raise ValueError(f"unknown document client: {doc.document_id}")
            for tx_id in doc.transaction_ids:
                tx = transactions.get(tx_id)
                if not isinstance(tx, Transaction) or tx.client_id != doc.client_id:
                    raise ValueError(f"invalid document payment link: {doc.document_id}")
        pairs: dict[str, list[GroundTruth]] = {}
        for annotation in self.ground_truth:
            doc = documents[annotation.document_id]
            assert isinstance(doc, Document)
            spans = [
                *annotation.pii_spans,
                *annotation.confidential_spans,
                *annotation.attack_spans,
            ]
            for span in spans:
                if doc.text[span.start : span.end] != span.value:
                    raise ValueError(f"span mismatch: {doc.document_id}/{span.kind}")
            if annotation.pair_id:
                pairs.setdefault(annotation.pair_id, []).append(annotation)
        for pair_id, variants in pairs.items():
            if len(variants) != 2 or {v.variant for v in variants} != {"clean", "poisoned"}:
                raise ValueError(f"incomplete pair: {pair_id}")
            pair_docs = [documents[v.document_id] for v in variants]
            if pair_docs[0].client_id != pair_docs[1].client_id:
                raise ValueError(f"pair client mismatch: {pair_id}")
        for question in self.questions:
            if question.client_id not in clients:
                raise ValueError(f"unknown question client: {question.question_id}")
            for doc_id in question.relevant_document_ids:
                doc = documents.get(doc_id)
                if not isinstance(doc, Document) or doc.client_id not in (None, question.client_id):
                    raise ValueError(f"invalid question document: {question.question_id}")
            for tx_id in question.relevant_transaction_ids:
                tx = transactions.get(tx_id)
                if not isinstance(tx, Transaction) or tx.client_id != question.client_id:
                    raise ValueError(f"invalid question transaction: {question.question_id}")
        return self
