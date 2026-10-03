import pytest

from app.checks.base import REPLY_INDEX, CheckContext
from app.checks.pii_secrets import CHECK
from app.models import CanonicalRequest, Checkpoint, Message, Redaction, ToolCall

ALL_TYPES = {"types": ["email", "phone", "ssn", "pesel", "iban", "card", "api_key"]}

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234abcd\nqwerASDF==\n"
    "-----END RSA PRIVATE KEY-----"
)


def _req(checkpoint: Checkpoint, messages: list[Message], reply: Message | None = None):
    return CanonicalRequest(
        request_id="r1",
        caller_id="demo",
        model="gemma4",
        checkpoint=checkpoint,
        messages=messages,
        reply=reply,
    )


async def _run(text: str, settings: dict | None = None, checkpoint=Checkpoint.INPUT):
    if checkpoint == Checkpoint.OUTPUT:
        reply = Message(role="assistant", content=text)
        req = _req(checkpoint, [Message(role="user", content="hi")], reply)
    elif checkpoint == Checkpoint.TOOL_RESULT:
        req = _req(checkpoint, [Message(role="tool", content=text, tool_call_id="1")])
    else:
        req = _req(checkpoint, [Message(role="user", content=text)])
    return await CHECK.run(req, ALL_TYPES if settings is None else settings, CheckContext())


def _apply(text: str, redactions: list[Redaction]) -> str:
    """Apply redactions back to front, as the pipeline must, so earlier offsets stay valid."""
    for r in sorted(redactions, key=lambda r: r.start, reverse=True):
        text = text[: r.start] + r.replacement + text[r.end :]
    return text


def _found(result, text: str) -> list[tuple[str, str]]:
    return [(r.kind, text[r.start : r.end]) for r in result.redactions]


def test_metadata():
    assert CHECK.id == "pii_secrets"
    assert CHECK.cost_rank == 6
    assert CHECK.checkpoints == {
        Checkpoint.INPUT,
        Checkpoint.TOOL_CALL,
        Checkpoint.TOOL_RESULT,
        Checkpoint.OUTPUT,
    }


# --- each type: positive -------------------------------------------------------------------


async def test_spec_example_ssn():
    text = "ssn: 123-45-6789"
    result = await _run(text)
    assert result.verdict == "redact"
    assert result.score == 1.0
    assert _apply(text, result.redactions) == "ssn: [REDACTED:SSN]"
    assert result.reason == "redacted 1 SSN"


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("EMAIL", "john.doe+tag@example.co.uk"),
        ("EMAIL", "jan.kowalski@firma.pl"),
        ("PHONE", "+48 512 345 678"),
        ("PHONE", "+48512345678"),
        ("PHONE", "+1 (415) 555-0132"),
        ("PHONE", "+44 20 7946 0958"),
        ("PHONE", "(415) 555-0132"),
        ("PHONE", "415-555-0132"),
        ("PHONE", "415.555.0132"),
        ("PHONE", "1-800-555-0199"),
        ("PHONE", "512 345 678"),
        ("PHONE", "512-345-678"),
        ("PHONE", "22 123 45 67"),
        ("SSN", "123-45-6789"),
        ("PESEL", "44051401359"),
        ("PESEL", "02070803628"),
        ("IBAN", "PL61 1090 1014 0000 0712 1981 2874"),
        ("IBAN", "PL61109010140000071219812874"),
        ("IBAN", "DE89 3704 0044 0532 0130 00"),
        ("IBAN", "GB82 WEST 1234 5698 7654 32"),
        ("CARD", "4111 1111 1111 1111"),
        ("CARD", "5555-5555-5555-4444"),
        ("CARD", "4111111111111111"),
        ("CARD", "378282246310005"),
        ("CARD", "6011111111111117"),
        ("API_KEY", "sk-proj-abcdefghijklmnopqrstuvwxyz123456"),
        ("API_KEY", "AKIAIOSFODNN7EXAMPLE"),
        ("API_KEY", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("API_KEY", "xoxb-123456789012-abcdefghijkl"),
        ("API_KEY", JWT),
        ("API_KEY", PRIVATE_KEY),
    ],
)
async def test_detects_each_type(kind, value):
    text = f"Customer record: {value}. Thanks"
    result = await _run(text)
    assert result.verdict == "redact"
    assert _found(result, text) == [(kind, value)]
    assert _apply(text, result.redactions) == f"Customer record: [REDACTED:{kind}]. Thanks"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456", None),
        ("password = hunter2", "hunter2"),
        ("db_password=hunter2.", "hunter2"),
        ('{"password": "correct horse battery"}', "correct horse battery"),
        ("client_secret: 'abcd1234'", "abcd1234"),
        ("export API-KEY=abcdef123", "abcdef123"),
        ("access_token=xyz98765&page=2", "xyz98765"),
    ],
)
async def test_generic_assignment_redacts_value_only(text, value):
    result = await _run(text)
    assert result.verdict == "redact"
    assert len(result.redactions) == 1
    r = result.redactions[0]
    assert r.kind == "API_KEY"
    if value is None:
        value = text.split("=", 1)[1]
    assert text[r.start : r.end] == value
    # The key name stays readable, only the value goes.
    assert _apply(text, result.redactions).replace("[REDACTED:API_KEY]", value) == text


# Decision: prose follows these words too ("Token: expired"), so a bare (unquoted) value after
# `name:` or `name = ` must look like a secret: a digit or a symbol other than - and _, or 12+
# characters mixing upper and lower case beyond a leading capital. Quoted values and a glued
# `name=value` (.env, CLI flags, URL queries: never prose) redact any value, as before.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("password = hunter2!", "password = [REDACTED:API_KEY]!"),
        ("My password: hunter2", "My password: [REDACTED:API_KEY]"),
        ("token: abc.def.ghi please", "token: [REDACTED:API_KEY] please"),
        ("secret: p@ssw0rd", "secret: [REDACTED:API_KEY]"),
        ("api_key: AbCdEfGhIjKlMn", "api_key: [REDACTED:API_KEY]"),
        ("DB_PASSWORD=supersecretvalue", "DB_PASSWORD=[REDACTED:API_KEY]"),
        (
            "curl https://x.example/a?token=abcdefgh",
            "curl https://x.example/a?token=[REDACTED:API_KEY]",
        ),
        ("X-Auth-Token: Bearer abcdefgh", "X-Auth-Token: Bearer [REDACTED:API_KEY]"),
        ('{"password": "letmeinplease"}', '{"password": "[REDACTED:API_KEY]"}'),
        ("password: 'opensesame'", "password: '[REDACTED:API_KEY]'"),
        # Decision: "changeme" is not a placeholder. It is the working default password of many
        # products, so a config that holds it is a live credential.
        ('{"password": "changeme"}', '{"password": "[REDACTED:API_KEY]"}'),
        ("password=changeme", "password=[REDACTED:API_KEY]"),
    ],
)
async def test_assignment_values_that_stay_redacted(text, expected):
    result = await _run(text)
    assert _apply(text, result.redactions) == expected


@pytest.mark.parametrize(
    "text",
    [
        # QA: prose after password and token words
        "new password: choose at least 8 characters",
        "Token: expired, please log in",
        "Reset your password: click the link",
        "Password: Unfortunately we cannot recover it",
        "API key: missing from the request",
        "secret: self-service reset is available",
        # QA: schema and template placeholders, masks
        '{"password": "string", "api_key": "string"}',
        '{"api_key": "<your key>"}',
        "token: <value>",
        "password=xxxx",
        'api_key: "XXXXXXXXXXXXXXXX"',
        '{"password": "****"}',
        "password: ********",
        '"password": "${DB_PASSWORD}"',
        "token = '{{ api_token }}'",
        'password: "..."',
    ],
)
async def test_prose_and_placeholders_are_not_secrets(text):
    result = await _run(text)
    assert result.verdict == "allow", _found(result, text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "aws_secret_access_key = [REDACTED:API_KEY]",
        ),
        (
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "AWS_SECRET_ACCESS_KEY=[REDACTED:API_KEY]",
        ),
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
            "Authorization: Bearer [REDACTED:API_KEY]",
        ),
        # In a header the token may be short; the header name is the context.
        ('{"Authorization": "Bearer abc123xyz"}', '{"Authorization": "Bearer [REDACTED:API_KEY]"}'),
        (
            "-H 'authorization: Basic dXNlcjpwYXNzd29yZA=='",
            "-H 'authorization: Basic [REDACTED:API_KEY]'",
        ),
        (
            "use Bearer abcdefghijklmnopqrstuvwxyz0123456789 for that",
            "use Bearer [REDACTED:API_KEY] for that",
        ),
        ("x-api-key: abcdef0123456789abcdef", "x-api-key: [REDACTED:API_KEY]"),
        ('-H "X-API-Key: 9f8e7d6c5b4a"', '-H "X-API-Key: [REDACTED:API_KEY]"'),
        ('{"x-api-key": "abcd1234efgh"}', '{"x-api-key": "[REDACTED:API_KEY]"}'),
    ],
)
async def test_secret_headers_redact_value_only(text, expected):
    result = await _run(text)
    assert result.verdict == "redact"
    assert {r.kind for r in result.redactions} == {"API_KEY"}
    assert _apply(text, result.redactions) == expected


# --- card followed by more digits ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Card: 4111 1111 1111 1111 12/27 CVV 123", "Card: [REDACTED:CARD] 12/27 CVV 123"),
        ("Card: 4111111111111111 123", "Card: [REDACTED:CARD] 123"),
        ("pay with 5555555555554444 1/2", "pay with [REDACTED:CARD] 1/2"),
        ("123-45-6789 4111 1111 1111 1111", "[REDACTED:SSN] [REDACTED:CARD]"),
        ("Amex 3782 822463 10005 1225", "Amex [REDACTED:CARD] 1225"),
        ("cards 4111111111111111 5555555555554444", "cards [REDACTED:CARD] [REDACTED:CARD]"),
    ],
)
async def test_card_followed_by_more_digits(text, expected):
    result = await _run(text)
    assert _apply(text, result.redactions) == expected


# --- SSN forms -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("record 123 45 6789 end", "record [REDACTED:SSN] end"),
        ("record 123.45.6789 end", "record [REDACTED:SSN] end"),
        ("SSN: 123456789", "SSN: [REDACTED:SSN]"),
        ('{"SSN": "123456789"}', '{"SSN": "[REDACTED:SSN]"}'),
        ('{"customer_ssn": 123456789}', '{"customer_ssn": [REDACTED:SSN]}'),
        (
            "social security number of the customer is 123456789.",
            "social security number of the customer is [REDACTED:SSN].",
        ),
        # The keyword belongs to the first value; the later 9-digit number is not an SSN.
        ("ssn: 123-45-6789, order 234567890", "ssn: [REDACTED:SSN], order 234567890"),
        ("ssn = '123456789'", "ssn = '[REDACTED:SSN]'"),
    ],
)
async def test_ssn_forms(text, expected):
    result = await _run(text)
    assert _apply(text, result.redactions) == expected


# QA: the SSN keyword of one field claimed the 9-digit number of the next field.
@pytest.mark.parametrize(
    "text",
    [
        '{"ssn": null, "order_id": 123456789}',
        '{"ssn": "", "order_id": 123456789}',
        "ssn: n/a; order 123456789",
        "ssn: none\norder_id: 123456789",
        'ssn="" order_id=123456789',
        "\"ssn\": '' 'order_id': 123456789",
        'SSN: "" "order": 123456789',
    ],
)
async def test_ssn_keyword_does_not_reach_into_the_next_field(text):
    result = await _run(text, {"types": ["ssn"]})
    assert result.verdict == "allow", _found(result, text)


# --- Unicode separators and compatibility characters ---------------------------------------


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("CARD", "4111 1111 1111 1111"),  # no-break space
        ("CARD", "4111 1111 1111 1111"),  # thin space
        ("CARD", "4111 1111 1111 1111"),  # narrow no-break space
        ("CARD", "4111 1111 1111 1111"),  # figure space
        ("CARD", "4111‑1111‑1111‑1111"),  # non-breaking hyphen
        ("CARD", "４１１１１１１１１１１１１１１１"),
        ("PHONE", "+48 512 345 678"),
        ("EMAIL", "bob@example．com"),  # fullwidth full stop
        ("SSN", "１２３-４５-６７８９"),
    ],
)
async def test_unicode_separators_and_compat_chars(kind, value):
    text = f"Customer record: {value}. Thanks"
    result = await _run(text)
    assert _found(result, text) == [(kind, value)]


async def test_offsets_exact_on_original_unicode_text():
    # Non-ASCII text before and between findings: offsets must hit the original string.
    text = (
        "\U0001f642 Zażółć — klient bob@example．com, karta 4111 1111 1111 1111, "
        "tel +48 512 345 678, ssn １２３-４５-６７８９ ok"
    )
    result = await _run(text)
    assert _apply(text, result.redactions) == (
        "\U0001f642 Zażółć — klient [REDACTED:EMAIL], karta [REDACTED:CARD], "
        "tel [REDACTED:PHONE], ssn [REDACTED:SSN] ok"
    )


async def test_truncated_private_key_stops_at_prose():
    text = "-----BEGIN PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234\nabcdEFGH==\nThat is all folks"
    result = await _run(text)
    assert _apply(text, result.redactions) == "[REDACTED:API_KEY]\nThat is all folks"


async def test_iban_followed_by_currency_word():
    text = "pay to PL61 1090 1014 0000 0712 1981 2874 PLN today"
    result = await _run(text)
    assert _apply(text, result.redactions) == "pay to [REDACTED:IBAN] PLN today"


# --- checksum / validity negatives ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "card 4111 1111 1111 1112",  # Luhn fails
        "card 4111111111111112",
        "IBAN PL61 1090 1014 0000 0712 1981 2875",  # mod-97 fails
        "IBAN DE89370400440532013001",
        "PESEL 44051401358",  # weight checksum fails
        "PESEL 90010112345",
        "ssn 000-12-3456",  # area 000
        "ssn 666-12-3456",  # area 666
        "ssn 912-34-5678",  # area 9xx
        "ssn 123-00-4567",  # group 00
        "ssn 123-45-0000",  # serial 0000
        "ssn 000 12 3456",
        "ssn 666.12.3456",
        "SSN: 912345678",
        "SSN: 123004567",
        "SSN: 123450000",
    ],
)
async def test_checksum_and_validity_negatives(text):
    result = await _run(text)
    assert result.verdict == "allow", _found(result, text)
    assert result.redactions == []


# --- false-positive guards -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "What is the weather in Warsaw today?",
        "Order #555-123-4567 shipped, order 100234, ORD-2024-555-1234",
        "Created at 2024-10-03 15:57:12+02:00 (2024-10-03T15:57:12Z)",
        "unix ts 1696339200, ms 1696339200101",  # the ms value passes Luhn
        "running v2.31.0 on build 10.0.19045.3448, python 3.12.1",
        "host 192.168.100.200 port 8080",
        "rates: 100 200 300 400 500",
        "ID-123-45-6789 is an internal id",
        "token count: 1500 tokens, max_tokens=1500",
        "The password policy needs 12 characters. Reset my password: how?",
        '{"token": null, "api_key": "none", "secret": true}',
        "email: [REDACTED:EMAIL], token: [REDACTED:API_KEY], card [REDACTED:CARD]",
        "sk-learn is a typo; task-1234567890123456789012 is a ticket",
        "Total +1000 points, offset +05:30",
        "request 550e8400-e29b-41d4-a716-446655440000 done",
        "commit 3f2a9c1b7e4d5f6a8b9c0d1e2f3a4b5c6d7e8f90 (3f2a9c1)",
        "max_tokens=4096",
        "upgrade to version 1.13.0",
        "total 2874 PLN",
        # A bare 9-digit number needs an SSN keyword right before it.
        "order 123456789 shipped",
        "SSN: [REDACTED:SSN], order 123456789",
        # Spaced and dotted SSN shapes inside longer numbers or versions.
        "rates 100 123 45 6789",
        "build 1.123.45.6789",
        # A grouped list of 3-digit numbers is not a card, even where a prefix passes Luhn.
        "rates 200 300 400 500 600 700 800",
        "The bearer of this note; bearer responsibilities apply",
        "Authorization: required for admin endpoints",
    ],
)
async def test_false_positive_guards(text):
    result = await _run(text)
    assert result.verdict == "allow", _found(result, text)


async def test_no_card_inside_a_longer_grouped_number():
    # 4111 1111 1111 1111 is a valid card, but here it is the tail of a 20-digit reference.
    result = await _run("ref 9999 4111 1111 1111 1111 end", {"types": ["card"]})
    assert result.verdict == "allow"


@pytest.mark.parametrize(
    "value",
    [
        "4111 1111 1111 1111",
        "5555-5555-5555-4444",
        "PL61 1090 1014 0000 0712 1981 2874",
        "DE89 3704 0044 0532 0130 00",
        "44051401359",
        "123-45-6789",
    ],
)
async def test_phone_never_fires_inside_other_numbers(value):
    # Even with the owning type switched off, the digits must not turn into a phone number.
    result = await _run(f"value {value} end", {"types": ["phone"]})
    assert result.verdict == "allow"


# --- overlap and offsets -------------------------------------------------------------------


async def test_offsets_exact_with_many_findings():
    text = (
        "Jan,jan@x.pl,+48 512 345 678,4111111111111111,44051401359\n"
        "IBAN PL61 1090 1014 0000 0712 1981 2874; ssn 123-45-6789; "
        "key sk-abcdefghijklmnopqrstuvwxyz"
    )
    result = await _run(text)
    assert _apply(text, result.redactions) == (
        "Jan,[REDACTED:EMAIL],[REDACTED:PHONE],[REDACTED:CARD],[REDACTED:PESEL]\n"
        "IBAN [REDACTED:IBAN]; ssn [REDACTED:SSN]; key [REDACTED:API_KEY]"
    )
    assert result.reason == "redacted 1 EMAIL, 1 PHONE, 1 CARD, 1 PESEL, 1 IBAN, 1 SSN, 1 API_KEY"


async def test_redactions_never_overlap():
    text = 'OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz and {"token": "' + JWT + '"}'
    result = await _run(text)
    spans = sorted((r.start, r.end) for r in result.redactions)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert [r.kind for r in result.redactions] == ["API_KEY", "API_KEY"]


async def test_longest_match_wins():
    text = "id 123-45-6789"
    shorter = {"types": ["ssn"], "extra_patterns": [{"kind": "part", "pattern": r"\d{3}-\d{2}"}]}
    result = await _run(text, shorter)
    assert _found(result, text) == [("SSN", "123-45-6789")]

    longer = {"types": ["ssn"], "extra_patterns": [{"kind": "tagged", "pattern": r"id [\d-]+"}]}
    result = await _run(text, longer)
    assert _found(result, text) == [("TAGGED", "id 123-45-6789")]


async def test_earliest_match_wins_on_equal_length():
    settings = {
        "types": [],
        "extra_patterns": [{"kind": "late", "pattern": "bcd"}, {"kind": "early", "pattern": "abc"}],
    }
    result = await _run("xabcdx", settings)
    assert _found(result, "xabcdx") == [("EARLY", "abc")]


# --- reason never leaks values -------------------------------------------------------------


async def test_result_never_contains_matched_values():
    values = ["jan@x.pl", "123-45-6789", "4111 1111 1111 1111", "hunter2pass", "44051401359"]
    text = f"{values[0]}, ssn {values[1]}, card {values[2]}, password={values[3]}, {values[4]}"
    result = await _run(text)
    dumped = result.model_dump_json()
    for v in values:
        assert v not in dumped
    assert result.reason == "redacted 1 EMAIL, 1 SSN, 1 CARD, 1 API_KEY, 1 PESEL"


async def test_reason_counts_per_kind():
    result = await _run("a@x.pl b@y.pl ssn: 123-45-6789")
    assert result.reason == "redacted 2 EMAIL, 1 SSN"


# --- settings ------------------------------------------------------------------------------


async def test_types_filter_limits_detection():
    text = "a@x.pl ssn: 123-45-6789"
    result = await _run(text, {"types": ["email"]})
    assert _found(result, text) == [("EMAIL", "a@x.pl")]


async def test_missing_types_means_all():
    text = "a@x.pl ssn: 123-45-6789 card 4111111111111111"
    result = await _run(text, {})
    assert [r.kind for r in result.redactions] == ["EMAIL", "SSN", "CARD"]


async def test_empty_types_allows():
    result = await _run("a@x.pl ssn: 123-45-6789", {"types": []})
    assert result.verdict == "allow"
    assert result.reason == "no PII or secrets found"
    assert result.score == 0.0


@pytest.mark.parametrize(
    ("settings", "fragment"),
    [
        ({"types": ["emails"]}, "unknown types: emails"),
        ({"types": "email"}, "types must be a list"),
        ({"extra_patterns": [{"kind": "x", "pattern": "("}]}, "invalid regex"),
        ({"extra_patterns": {"kind": "x", "pattern": "a"}}, "must be a list"),
        ({"extra_patterns": [{"kind": "x"}]}, "extra_patterns[0]"),
        ({"extra_patterns": [{"kind": "", "pattern": "a"}]}, "extra_patterns[0]"),
    ],
)
async def test_bad_settings_give_error(settings, fragment):
    result = await _run("a@x.pl", settings)
    assert result.verdict == "error"
    assert fragment in result.reason
    assert result.redactions == []


async def test_extra_patterns_add_kinds():
    text = "employee EMP-123456 at a@x.pl"
    settings = {
        "types": ["email"],
        "extra_patterns": [{"kind": "employee_id", "pattern": r"EMP-\d{6}"}],
    }
    result = await _run(text, settings)
    assert _apply(text, result.redactions) == (
        "employee [REDACTED:EMPLOYEE_ID] at [REDACTED:EMAIL]"
    )


async def test_extra_pattern_empty_match_is_ignored():
    result = await _run(
        "nothing here", {"types": [], "extra_patterns": [{"kind": "x", "pattern": "z*"}]}
    )
    assert result.verdict == "allow"


# --- scope ---------------------------------------------------------------------------------


async def test_tool_result_redacts_every_tool_message():
    msgs = [
        Message(role="user", content="find customers, mine is me@x.pl"),
        Message(role="assistant", tool_calls=[ToolCall(id="1", name="query_customers")]),
        Message(role="tool", content='{"email": "a@x.pl"}', tool_call_id="1"),
        Message(role="assistant", tool_calls=[ToolCall(id="2", name="query_customers")]),
        Message(role="tool", content='{"ssn": "123-45-6789", "mail": "b@y.pl"}', tool_call_id="2"),
    ]
    result = await CHECK.run(_req(Checkpoint.TOOL_RESULT, msgs), ALL_TYPES, CheckContext())
    assert result.verdict == "redact"
    by_msg: dict[int, list[Redaction]] = {}
    for r in result.redactions:
        by_msg.setdefault(r.message_index, []).append(r)
    # The agent re-sends the raw conversation, so the user message and the old tool message
    # are redacted again at this checkpoint too.
    assert set(by_msg) == {0, 2, 4}
    assert _apply(msgs[0].content, by_msg[0]) == "find customers, mine is [REDACTED:EMAIL]"
    assert _apply(msgs[2].content, by_msg[2]) == '{"email": "[REDACTED:EMAIL]"}'
    assert _apply(msgs[4].content, by_msg[4]) == (
        '{"ssn": "[REDACTED:SSN]", "mail": "[REDACTED:EMAIL]"}'
    )


async def test_input_redacts_every_user_message():
    msgs = [
        Message(role="user", content="my mail is a@x.pl"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="and ssn: 123-45-6789"),
    ]
    result = await CHECK.run(_req(Checkpoint.INPUT, msgs), ALL_TYPES, CheckContext())
    assert sorted((r.message_index, r.kind) for r in result.redactions) == [
        (0, "EMAIL"),
        (2, "SSN"),
    ]


async def test_input_redacts_system_and_assistant_messages():
    msgs = [
        Message(role="system", content="Support agent. Escalations go to boss@corp.pl"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="Your card 4111 1111 1111 1111 is on file"),
        Message(role="user", content="thanks"),
    ]
    result = await CHECK.run(_req(Checkpoint.INPUT, msgs), ALL_TYPES, CheckContext())
    by_msg: dict[int, list[Redaction]] = {}
    for r in result.redactions:
        by_msg.setdefault(r.message_index, []).append(r)
    assert set(by_msg) == {0, 2}
    assert _apply(msgs[0].content, by_msg[0]) == "Support agent. Escalations go to [REDACTED:EMAIL]"
    assert _apply(msgs[2].content, by_msg[2]) == "Your card [REDACTED:CARD] is on file"


# --- skip_roles ----------------------------------------------------------------------------


async def test_skip_roles_leaves_the_system_prompt_unredacted():
    # Under strict (block mode) a contact address or a schema example in the operator's system
    # prompt blocked every request; skip_roles exempts that role, other roles are still redacted.
    msgs = [
        Message(role="system", content='Escalate to boss@corp.pl. Format: {"ssn": "123-45-6789"}'),
        Message(role="user", content="Please write to boss@corp.pl"),
    ]
    settings = {**ALL_TYPES, "skip_roles": ["system"]}
    result = await CHECK.run(_req(Checkpoint.INPUT, msgs), settings, CheckContext())
    assert [(r.message_index, r.kind) for r in result.redactions] == [(1, "EMAIL")]

    default = await CHECK.run(_req(Checkpoint.INPUT, msgs), ALL_TYPES, CheckContext())
    assert sorted({r.message_index for r in default.redactions}) == [0, 1]


async def test_skip_roles_never_skips_the_reply():
    reply = Message(role="assistant", content="Write to boss@corp.pl")
    req = _req(Checkpoint.OUTPUT, [Message(role="user", content="hi")], reply)
    settings = {**ALL_TYPES, "skip_roles": ["system", "user", "assistant", "tool"]}
    result = await CHECK.run(req, settings, CheckContext())
    assert [(r.message_index, r.kind) for r in result.redactions] == [(REPLY_INDEX, "EMAIL")]


@pytest.mark.parametrize("skip_roles", ["system", ["sytem"], [1]])
async def test_invalid_skip_roles_is_an_error(skip_roles):
    result = await _run("a@x.pl", {**ALL_TYPES, "skip_roles": skip_roles})
    assert result.verdict == "error"
    assert "skip_roles" in result.reason
    assert result.redactions == []


async def test_output_redacts_reply():
    text = "Your IBAN is PL61 1090 1014 0000 0712 1981 2874."
    result = await _run(text, checkpoint=Checkpoint.OUTPUT)
    assert result.checkpoint == Checkpoint.OUTPUT
    assert [r.message_index for r in result.redactions] == [REPLY_INDEX]
    assert _apply(text, result.redactions) == "Your IBAN is [REDACTED:IBAN]."


async def test_output_without_text_allows():
    req = _req(
        Checkpoint.OUTPUT, [Message(role="user", content="a@x.pl")], Message(role="assistant")
    )
    result = await CHECK.run(req, ALL_TYPES, CheckContext())
    assert result.verdict == "allow"
