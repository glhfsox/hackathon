"""PII and secrets redaction (docs/architecture.md §4, rank 6).

Finds personal data and credentials in the checkpoint's texts and returns one Redaction per
finding. The built-in detectors live in code because their precision comes from checksums and
boundary rules a policy regex cannot express. The policy picks which of them run (`types`) and may
add its own regexes (`extra_patterns`).

All detectors and policy regexes run on a folded copy of each text (see `_Fold`) that has the same
length as the original, so every offset they report is exact for the original text.

Known limitation: each message text is scanned as one string, so a value split across fields
(`{"a": "123-45", "b": "6789"}`) is not recognised.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any, get_args

from app.checks.base import REPLY_INDEX, CheckContext, make_result, targets
from app.models import CanonicalRequest, Checkpoint, CheckResult, Message, Redaction

# (start, end, KIND). Only offsets are kept so a matched value can never leak into the result.
Span = tuple[int, int, str]

_ROLES = frozenset(get_args(Message.model_fields["role"].annotation))

# --- folding ---------------------------------------------------------------------------------


class _Fold(dict[int, str]):
    """str.translate table mapping each character to the one character it stands for.

    No-break, thin and figure spaces become " ", Unicode dashes "-", any decimal digit its ASCII
    digit, and fullwidth and other compatibility forms ("．", "＠") their NFKC character. ASCII
    and characters whose NFKC form is longer stay as they are, so folding never changes the
    length of a text. Entries are filled on first use.
    """

    def __missing__(self, code: int) -> str:
        ch = chr(code)
        if code < 128:
            folded = ch
        elif ch.isspace():
            folded = " "
        elif unicodedata.category(ch) == "Pd":
            folded = "-"
        elif ch.isdecimal():
            folded = str(int(ch))
        else:
            nfkc = unicodedata.normalize("NFKC", ch)
            folded = nfkc if len(nfkc) == 1 else ch
        self[code] = folded
        return folded


_FOLD = _Fold()


def _fold(text: str) -> str:
    return text if text.isascii() else text.translate(_FOLD)


# --- email -----------------------------------------------------------------------------------

# The lookbehind makes the engine start only at the beginning of a word run, which keeps long
# texts without "@" linear.
_EMAIL = re.compile(r"(?<![\w.%+-])[\w.%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])")


def _find_email(text: str) -> Iterator[Span]:
    for m in _EMAIL.finditer(text):
        yield m.start(), m.end(), "EMAIL"


# --- US SSN ----------------------------------------------------------------------------------

# Area 000, 666 and 9xx, group 00 and serial 0000 are never issued.
_SSN_SEPARATED = (
    # 123-45-6789
    re.compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])"),
    # 123 45 6789, 123.45.6789. Spaces and dots also group longer numbers and versions, so these
    # forms must not follow "<digit> " or "." ("100 123 45 6789", "1.123.45.6789").
    re.compile(
        r"(?<![\w.-])(?<!\d )(?!000|666|9\d\d)\d{3}([ .])(?!00)\d{2}\1(?!0000)\d{4}(?![\w-]|\.\d)"
    ),
)
# 123456789 could be any 9-digit number (order id, zip+4), so it counts only right after an SSN
# keyword: at most 32 characters in between, none of them a digit or "]" (the keyword cannot
# belong to an earlier value or a "[REDACTED:SSN]" marker), and nothing that ends the keyword's
# own field: "," ";" a line break, or a closing quote followed by a new key
# (`{"ssn": null, "order_id": 123456789}`, `ssn="" order_id=123456789`). Only the number is
# redacted.
_NEXT_KEY = r"[\"'][ \t]{0,8}[\"']?[A-Za-z_][\w-]{0,63}[\"']?[ \t]{0,8}[:=]"
_SSN_BARE = re.compile(
    r"(?<![A-Za-z])(?:ssn|social[\s_-]*security)(?![A-Za-z])"
    rf"(?:(?!{_NEXT_KEY})[^\d\],;\r\n]){{0,32}}"
    r"(?<![\w.-])(?P<value>(?!000|666|9\d\d)\d{3}(?!00)\d{2}(?!0000)\d{4})(?![\w-]|\.\d)",
    re.IGNORECASE,
)


def _find_ssn(text: str) -> Iterator[Span]:
    for pattern in _SSN_SEPARATED:
        for m in pattern.finditer(text):
            yield m.start(), m.end(), "SSN"
    for m in _SSN_BARE.finditer(text):
        yield m.start("value"), m.end("value"), "SSN"


# --- PESEL -----------------------------------------------------------------------------------

_PESEL = re.compile(r"(?<![\w.])\d{11}(?!\w|\.\d)")
_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)


def _pesel_ok(digits: str) -> bool:
    total = sum(int(d) * w for d, w in zip(digits, _PESEL_WEIGHTS, strict=False))
    return (10 - total % 10) % 10 == int(digits[10])


def _find_pesel(text: str) -> Iterator[Span]:
    for m in _PESEL.finditer(text):
        if _pesel_ok(m.group()):
            yield m.start(), m.end(), "PESEL"


# --- IBAN ------------------------------------------------------------------------------------

_IBAN = re.compile(r"(?<!\w)[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?!\w)")


def _iban_ok(compact: str) -> bool:
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def _find_iban(text: str) -> Iterator[Span]:
    for m in _IBAN.finditer(text):
        value = m.group()
        # A trailing uppercase word ("... 2874 PLN") can be swallowed as a last group; retry
        # without trailing space-separated chunks before giving up.
        while True:
            if _iban_ok(value.replace(" ", "")):
                yield m.start(), m.start() + len(value), "IBAN"
                break
            if " " not in value:
                break
            value = value.rsplit(" ", 1)[0]


# --- payment card ----------------------------------------------------------------------------

# A card is 13-19 digits inside a run of digit blocks joined by single spaces or dashes. Its first
# digit 2-6 covers every major network and rules out millisecond timestamps (which start with 1)
# and many order ids. A run glued to a word char, ".", or "-" is part of an id, version or decimal.
_NUMBER_RUN = re.compile(r"\d+(?:[ -]\d+)*")
_DIGIT_BLOCK = re.compile(r"\d+")
_GLUED_BEFORE = re.compile(r"[\w.-]")
_GLUED_AFTER = re.compile(r"[\w-]|\.\d")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card_may_start(text: str, blocks: list[tuple[int, int]], i: int, glued: bool) -> bool:
    start = blocks[i][0]
    if text[start] not in "23456":
        return False
    if i == 0:
        return not glued
    # Inside a run a card starts only after a space, and only where a new number plausibly
    # begins: a lone 13+ digit block, or the first block after a change of separator
    # ("123-45-6789 4111 1111 1111 1111"). Otherwise "9999 4111 1111 1111 1111" would yield the
    # tail of a longer grouped reference.
    if text[start - 1] != " ":
        return False
    return blocks[i][1] - start >= 13 or (i >= 2 and text[blocks[i - 1][0] - 1] != " ")


def _card_end(text: str, blocks: list[tuple[int, int]], i: int, run_end_ok: bool) -> int | None:
    """Last block of the longest Luhn-valid card starting at block i, or None.

    Candidates end only at block boundaries, so "4111 1111 1111 1111 12/27" yields the card and
    leaves the expiry. A candidate shorter than the whole run must be grouped like a card (every
    block but its last has 4+ digits): "200 300 400 500 600 700 800" has a Luhn-valid prefix.
    """
    ends = []
    digits = 0
    for j in range(i, len(blocks)):
        digits += blocks[j][1] - blocks[j][0]
        if digits > 19:
            break
        if digits >= 13 and (j < len(blocks) - 1 or run_end_ok):
            ends.append(j)
    for j in reversed(ends):
        whole_run = i == 0 and j == len(blocks) - 1
        if not whole_run and any(end - start < 4 for start, end in blocks[i:j]):
            continue
        if _luhn_ok("".join(text[start:end] for start, end in blocks[i : j + 1])):
            return j
    return None


def _find_card(text: str) -> Iterator[Span]:
    for run in _NUMBER_RUN.finditer(text):
        blocks = [m.span() for m in _DIGIT_BLOCK.finditer(text, run.start(), run.end())]
        glued = run.start() > 0 and _GLUED_BEFORE.match(text, run.start() - 1) is not None
        run_end_ok = _GLUED_AFTER.match(text, run.end()) is None
        i = 0
        while i < len(blocks):
            j = None
            if _card_may_start(text, blocks, i, glued):
                j = _card_end(text, blocks, i, run_end_ok)
            if j is None:
                i += 1
                continue
            yield blocks[i][0], blocks[j][1], "CARD"
            i = j + 1


# --- phone -----------------------------------------------------------------------------------

# Shared guards: never start right after a word char, "#", "/", ".", "+" or "-" (order ids,
# paths, versions) or after "<digit><separator>" (inside a longer number), and never end where
# the number continues.
_PHONE_BEFORE = r"(?<![\w#./+-])(?<!\d[ .-])"
_PHONE_AFTER = r"(?![\w-]|[ .-]?\d)"
_PHONE_INTL = re.compile(
    r"(?<![\w+])\+[1-9]\d{0,3}(?:[ .-]?\(\d{1,4}\))?(?:[ .-]?\d{1,4}){1,6}" + _PHONE_AFTER
)
_PHONE_LOCAL = (
    # US: (415) 555-0132, 415-555-0132, 415.555.0132, 1-800-555-0199. Area codes start 2-9.
    re.compile(
        _PHONE_BEFORE
        + r"(?:1[ .-])?(?:\([2-9]\d{2}\) ?|[2-9]\d{2}[ .-])\d{3}[ .-]\d{4}"
        + _PHONE_AFTER
    ),
    # PL mobile: 512 345 678, 512-345-678. Mobile prefixes start 4-8.
    re.compile(_PHONE_BEFORE + r"[4-8]\d{2}([ -])\d{3}\1\d{3}" + _PHONE_AFTER),
    # PL landline: 22 123 45 67, (22) 123-45-67.
    re.compile(
        _PHONE_BEFORE + r"(?:\([1-9]\d\) ?|[1-9]\d[ -])\d{3}[ -]\d{2}[ -]\d{2}" + _PHONE_AFTER
    ),
)


def _find_phone(text: str) -> Iterator[Span]:
    for m in _PHONE_INTL.finditer(text):
        # E.164 allows at most 15 digits; fewer than 8 is a count or an offset, not a number.
        if 8 <= sum(ch.isdigit() for ch in m.group()) <= 15:
            yield m.start(), m.end(), "PHONE"
    for pattern in _PHONE_LOCAL:
        for m in pattern.finditer(text):
            yield m.start(), m.end(), "PHONE"


# --- API keys and other secrets --------------------------------------------------------------

_SECRET_TOKENS = (
    re.compile(r"(?<![\w-])sk-[A-Za-z0-9][A-Za-z0-9_-]{19,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    # A complete block, or a truncated one: the header plus the base64-only lines that follow.
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
        r"(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        r"|(?:[ \t]*\r?\n[A-Za-z0-9+/=]+(?=[ \t]*(?:\r?\n|$)))*)",
        re.DOTALL,
    ),
)
# `name = value` / `"name": "value"`; only the value is redacted. The name may carry a prefix
# (client_secret, OPENAI_API_KEY) but must end in the keyword, so `max_tokens=` does not match.
# A bare value may follow an auth scheme (`X-Auth-Token: Bearer abc`); the scheme is kept.
_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]*?"
    r"(?:api[_-]?key|access[_-]?key|secret(?:[_-]?key)?|token|passw(?:or)?d)"
    r"[\"']?(?P<sep>\s*[:=]\s*)"
    r"(?:\"(?P<dq>[^\"\n]+)\"|'(?P<sq>[^'\n]+)'"
    r"|(?P<scheme>(?:bearer|basic)\s+)?(?P<bare>[^\s\"',;&{}()<>\[\]]+))",
    re.IGNORECASE,
)
# `Authorization: Bearer <token>` (or Basic, Token) and a bare `Bearer <token>`; only the
# credential is redacted. "Bearer" is also an English word, so without the header the token
# must be 16+ characters and contain a digit.
_AUTH_HEADER = re.compile(
    r"(?<![A-Za-z0-9_-])authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+"
    r"(?P<value>[A-Za-z0-9._~+/=-]{4,})",
    re.IGNORECASE,
)
_BEARER = re.compile(
    r"(?<![A-Za-z0-9_-])bearer\s+(?P<value>(?=[A-Za-z._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,})",
    re.IGNORECASE,
)
# Values that mean "no secret here": empty markers, schema and template placeholders, masks and
# our own markers; redacting them would only add noise. "changeme" is deliberately not one: it is
# the working default password of many products, so a config holding it holds a live credential.
_PLACEHOLDER = re.compile(
    r"null|none|true|false|undefined|string|<[^<>]*>|\$\{[^{}]*\}|\{\{[^{}]*\}\}"
    r"|x{3,}|\*{3,}|\.{3,}|•{3,}|\[REDACTED.*",
    re.IGNORECASE,
)
_MIN_ASSIGNED_SECRET = 4
_MIN_LETTERS_ONLY_SECRET = 12


def _secret_shaped(value: str) -> bool:
    """Whether a bare value after `name:` or `name = ` looks like a secret rather than prose.

    Prose follows these words too ("new password: choose ...", "Token: expired"), so the value
    must hold a digit or a symbol other than - and _, or be 12+ characters that mix upper and
    lower case beyond a leading capital. Missed by design: a bare secret that is one plain word or
    lowercase words joined by - or _, after `:` or a spaced `=`. Quoted values, a glued
    `name=value` and a value after an auth scheme are redacted whatever their shape.
    """
    if any(not (ch.isalpha() or ch in "-_") for ch in value):
        return True
    return (
        len(value) >= _MIN_LETTERS_ONLY_SECRET
        and any(ch.islower() for ch in value)
        and any(ch.isupper() for ch in value[1:])
    )


def _find_api_key(text: str) -> Iterator[Span]:
    for pattern in _SECRET_TOKENS:
        for m in pattern.finditer(text):
            yield m.start(), m.start() + len(m.group().rstrip()), "API_KEY"
    for pattern in (_AUTH_HEADER, _BEARER):
        for m in pattern.finditer(text):
            yield m.start("value"), m.end("value"), "API_KEY"
    for m in _ASSIGNMENT.finditer(text):
        group = next(g for g in ("dq", "sq", "bare") if m.group(g) is not None)
        start, end = m.span(group)
        if group == "bare":
            # Sentence punctuation after a bare value is not part of it.
            end = start + len(m.group(group).rstrip(".:!?"))
        value = text[start:end]
        if len(value) < _MIN_ASSIGNED_SECRET or _PLACEHOLDER.fullmatch(value):
            continue
        # `name=value` with nothing around "=" is code, config or a URL query, never prose.
        prose_possible = m.group("sep") != "=" and m.group("scheme") is None
        if group == "bare" and prose_possible and not _secret_shaped(value):
            continue
        yield start, end, "API_KEY"


_DETECTORS: dict[str, Callable[[str], Iterator[Span]]] = {
    "email": _find_email,
    "phone": _find_phone,
    "ssn": _find_ssn,
    "pesel": _find_pesel,
    "iban": _find_iban,
    "card": _find_card,
    "api_key": _find_api_key,
}


def _resolve(spans: list[Span]) -> list[Span]:
    """Pick non-overlapping spans: longest first, then earliest.

    PHONE always loses to other kinds, so a phone pattern can never take digits that belong to
    a card, IBAN, PESEL or SSN.
    """
    chosen: list[Span] = []
    order = sorted(set(spans), key=lambda s: (s[2] == "PHONE", s[0] - s[1], s[0], s[2]))
    for span in order:
        if all(span[1] <= c[0] or span[0] >= c[1] for c in chosen):
            chosen.append(span)
    return sorted(chosen)


def _compile_extras(raw: Any) -> tuple[list[tuple[str, re.Pattern[str]]], str | None]:
    """Policy regexes as (KIND, pattern), or an error message for the first bad entry."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "extra_patterns must be a list of {kind, pattern}"
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for i, item in enumerate(raw):
        kind = item.get("kind") if isinstance(item, dict) else None
        pattern = item.get("pattern") if isinstance(item, dict) else None
        if not isinstance(kind, str) or not kind.strip() or not isinstance(pattern, str):
            return [], f"extra_patterns[{i}] needs a non-empty string 'kind' and a 'pattern'"
        try:
            compiled.append((kind.strip().upper(), re.compile(pattern)))
        except re.error as exc:
            return [], f"extra_patterns[{i}] ({kind}): invalid regex: {exc}"
    return compiled, None


class PiiSecretsCheck:
    id = "pii_secrets"
    cost_rank = 6
    checkpoints = frozenset({Checkpoint.INPUT, Checkpoint.TOOL_RESULT, Checkpoint.OUTPUT})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        kinds = settings.get("types")
        if kinds is None:
            kinds = list(_DETECTORS)
        if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
            return make_result(
                self.id, request, "error", "types must be a list of type names", started
            )
        unknown = sorted(set(kinds) - set(_DETECTORS))
        if unknown:
            return make_result(
                self.id, request, "error", f"unknown types: {', '.join(unknown)}", started
            )
        extras, problem = _compile_extras(settings.get("extra_patterns"))
        if problem:
            return make_result(self.id, request, "error", problem, started)
        skip_roles = settings.get("skip_roles", [])
        if not isinstance(skip_roles, list) or not all(isinstance(r, str) for r in skip_roles):
            return make_result(
                self.id, request, "error", "skip_roles must be a list of role names", started
            )
        unknown_roles = sorted(set(skip_roles) - _ROLES)
        if unknown_roles:
            reason = f"unknown skip_roles: {', '.join(unknown_roles)}"
            return make_result(self.id, request, "error", reason, started)

        redactions: list[Redaction] = []
        # scope="all": the agent re-sends the raw conversation, so earlier messages must be
        # redacted again on every step. skip_roles exempts history the operator wrote (a contact
        # address or a schema example in the system prompt); the model's reply (REPLY_INDEX) is
        # always redacted.
        for index, text in targets(request, scope="all"):
            if index != REPLY_INDEX and request.messages[index].role in skip_roles:
                continue
            folded = _fold(text)
            spans = [span for kind in kinds for span in _DETECTORS[kind](folded)]
            spans += [(m.start(), m.end(), k) for k, p in extras for m in p.finditer(folded)]
            for start, end, kind in _resolve([s for s in spans if s[0] < s[1]]):
                redactions.append(
                    Redaction(
                        kind=kind,
                        start=start,
                        end=end,
                        replacement=f"[REDACTED:{kind}]",
                        message_index=index,
                    )
                )

        if not redactions:
            return make_result(self.id, request, "allow", "no PII or secrets found", started)
        # Kinds and counts only: the reason is shown in the UI and audit log, values never are.
        counts = Counter(r.kind for r in redactions)
        reason = "redacted " + ", ".join(f"{n} {kind}" for kind, n in counts.items())
        return make_result(self.id, request, "redact", reason, started, redactions=redactions)


CHECK = PiiSecretsCheck()
