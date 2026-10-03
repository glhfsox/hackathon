# Synthetic treasury corpus (v1)

Internal demo-data interface; middleware HTTP shapes are unchanged. Models reject unknown fields. IDs are stable strings; timestamps are ISO 8601 UTC; all data is synthetic.

## Source records

| Entity | Fields |
|--------|--------|
| Client | `client_id`, `name`, `country` (`PL` or `US`), `sector` |
| Contact | `contact_id`, `client_id`, `name`, `email`, `phone`, `identifier_kind` (`PESEL` or `SSN`), `identifier` |
| Account | `account_id`, `client_id`, `iban`, `currency` (`PLN`, `EUR`, `USD`), `opening_balance_minor` (nonnegative integer) |
| Transaction | `transaction_id`, `account_id`, `client_id`, `counterparty`, `amount_minor` (positive integer), `currency`, `direction` (`credit` or `debit`), `status` (`settled`, `pending`, `held`), `booked_at`, `reference` |
| Document | `document_id`, `client_id` (nullable for guides), `title`, `document_type`, `classification` (`public`, `internal`, `confidential`, `restricted`), `source` (`internal_memo`, `client_record`, `supplier_email`, `public_guide`), `created_at`, `transaction_ids` (list), `text` |

Contact emails end in `.example`; account IBANs are fictional PL/GB examples with valid checksums. Currencies have two decimal places in this demo; amounts are integer minor units. Transaction client/currency match the account. Client-specific documents and linked payments have the same client.

## Evaluation records (never indexed)

- `Span`: `kind`, `start`, `end`, `value`. Offsets are Python Unicode code points into exact text, zero-based, end-exclusive. Do not normalize before using offsets.
- `GroundTruth`: `document_id`, `pii_spans`, `confidential_spans`, `attack_spans` (Span lists), `attack_family` (nullable), `pair_id` (nullable), `variant` (`clean`, `poisoned`, or null). Spans match `text[start:end]`. Poisoned variants have a family and spans; clean variants do not.
- `RetrievalQuestion`: `question_id`, `client_id`, `query`, `relevant_document_ids`, `relevant_transaction_ids`, `answer_facts` (string map). Relevant IDs identify sources, not guaranteed ranking. No middleware verdict without a policy fixture.

## Configuration

`schema_version` is `1`; `seed` is an integer; `clients` is at least 4 (attack-family coverage); `transactions` is at least `clients` (investigation payment per client); `reference_date` is an ISO date. Defaults live only in `demo_data/config.json`.

## Artifacts

- `records/{clients,contacts,accounts,transactions}.jsonl`: source records.
- `documents/<document_id>.md`: exact text without evaluator annotations.
- `documents.jsonl`: Document fields except `text`, replaced by relative `text_path`.
- `evaluation/ground_truth.jsonl`, `evaluation/questions.jsonl`: evaluator-only.
- `corpus.sqlite3`: `clients`, `contacts`, `accounts`, `transactions`, `documents`, `document_transactions`; full document text and foreign keys. No evaluation records.
- `manifest.json`: `schema_version`, `generator_version`, `faker_version`, `config`, entity `counts`, and `files` mapping relative paths to SHA-256. Excludes itself.

Output refuses existing paths. Manifest is written last; a directory without it is incomplete. Validation checks models, references, source spans, hashes, and snapshot agreement.
