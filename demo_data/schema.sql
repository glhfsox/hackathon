CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.clients (
    client_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL CHECK (country IN ('PL', 'US')),
    sector TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.contacts (
    contact_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES {schema}.clients(client_id),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    identifier_kind TEXT NOT NULL CHECK (identifier_kind IN ('PESEL', 'SSN')),
    identifier TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.accounts (
    account_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES {schema}.clients(client_id),
    iban TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency IN ('PLN', 'EUR', 'USD')),
    opening_balance_minor BIGINT NOT NULL CHECK (opening_balance_minor >= 0),
    UNIQUE (account_id, client_id, currency)
);

CREATE TABLE IF NOT EXISTS {schema}.transactions (
    transaction_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL REFERENCES {schema}.clients(client_id),
    counterparty TEXT NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (currency IN ('PLN', 'EUR', 'USD')),
    direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
    status TEXT NOT NULL CHECK (status IN ('settled', 'pending', 'held')),
    booked_at TIMESTAMPTZ NOT NULL,
    reference TEXT NOT NULL,
    FOREIGN KEY (account_id, client_id, currency)
        REFERENCES {schema}.accounts(account_id, client_id, currency),
    UNIQUE (transaction_id, client_id)
);

CREATE TABLE IF NOT EXISTS {schema}.documents (
    document_id TEXT PRIMARY KEY,
    client_id TEXT REFERENCES {schema}.clients(client_id),
    title TEXT NOT NULL,
    document_type TEXT NOT NULL,
    classification TEXT NOT NULL
        CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
    source TEXT NOT NULL
        CHECK (source IN ('internal_memo', 'client_record', 'supplier_email', 'public_guide')),
    created_at TIMESTAMPTZ NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (document_id, client_id)
);

CREATE TABLE IF NOT EXISTS {schema}.document_transactions (
    document_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    PRIMARY KEY (document_id, transaction_id),
    FOREIGN KEY (document_id, client_id)
        REFERENCES {schema}.documents(document_id, client_id),
    FOREIGN KEY (transaction_id, client_id)
        REFERENCES {schema}.transactions(transaction_id, client_id)
);

CREATE INDEX IF NOT EXISTS transactions_client ON {schema}.transactions(client_id);
CREATE INDEX IF NOT EXISTS documents_client ON {schema}.documents(client_id);
