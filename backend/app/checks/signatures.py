"""Signatures: block text that matches the external attack-signature feed (architecture §4, #4)."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, get_args

from app.checks.base import REPLY_INDEX, CheckContext, make_result, targets
from app.models import CanonicalRequest, Checkpoint, CheckResult, Message
from app.signatures import CATEGORIES

_ROLES = frozenset(get_args(Message.model_fields["role"].annotation))

# Cyrillic and Greek letters that render like Latin ones; NFKC leaves them alone.
_HOMOGLYPHS = str.maketrans(
    # Cyrillic a e o p c x y i j s h d q w, then A B E K M H O P C T X Y I J S
    "\u0430\u0435\u043e\u0440\u0441\u0445\u0443\u0456\u0458\u0455\u04bb\u0501\u051b\u051d"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0423\u0406\u0408\u0405"
    # Greek a i k v o p u x, then A B E Z H I K M N O P T Y X
    "\u03b1\u03b9\u03ba\u03bd\u03bf\u03c1\u03c5\u03c7"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7",
    "aeopcxyijshdqwABEKMHOPCTXYIJSaikvopuxABEZHIKMNOPTYX",
)
_HOMOGLYPH_CHARS = re.compile("[" + "".join(map(chr, _HOMOGLYPHS)) + "]")
# Zero-width space, non-joiner and joiner, LRM, RLM, word joiner, BOM, soft hyphen: invisible to
# a human, so they hide inside a keyword ("ig\u200bnore") or stand in for the spaces between
# words. A text that has them therefore gets two normalized forms: invisibles dropped and turned
# into spaces.
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]")


def _forms(text: str) -> list[str]:
    """The text plus its normalized forms, each distinct form once.

    The original is always kept, so a signature that looks for the obfuscation itself (PI-006,
    invisible tag characters) still sees it. Normalization only undoes cheap visual tricks:
    compatibility forms (NFKC: fullwidth, math letters, ligatures), invisible characters and
    look-alike letters. Leetspeak, translation and paraphrase are Jev's job, not a regex's.
    Every scan of a 200 KB text costs tens of milliseconds, so a step that would change nothing
    is skipped and a copy is only made (and scanned) when it differs from the original.
    """
    if text.isascii():
        # Nothing to normalize, so ordinary traffic is scanned once.
        return [text]
    base = text
    if not unicodedata.is_normalized("NFKC", base):
        base = unicodedata.normalize("NFKC", base)
    if _HOMOGLYPH_CHARS.search(base):
        base = base.translate(_HOMOGLYPHS)
    copies = [base]
    if _INVISIBLE.search(base):
        copies = [_INVISIBLE.sub("", base), _INVISIBLE.sub(" ", base)]
    return list(dict.fromkeys((text, *copies)))


class SignaturesCheck:
    id = "signatures"
    cost_rank = 4
    checkpoints = frozenset({Checkpoint.input, Checkpoint.tool_call, Checkpoint.tool_result})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        if ctx.signatures is None:
            # Never allow on a feed that did not load: the mode decides block or flag.
            return make_result(self.id, request, "error", "signature feed not loaded", started)

        categories = settings.get("categories")
        if categories is not None:
            if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
                reason = "setting 'categories' must be a list of category names"
                return make_result(self.id, request, "error", reason, started)
            # A typo would silently switch part of the feed off, so it is an error instead.
            unknown = sorted(set(categories) - CATEGORIES)
            if unknown:
                reason = f"setting 'categories' has unknown categories: {', '.join(unknown)}"
                return make_result(self.id, request, "error", reason, started)
        active = [s for s in ctx.signatures if categories is None or s.category in categories]

        skip_roles = settings.get("skip_roles", [])
        if not isinstance(skip_roles, list) or not all(isinstance(r, str) for r in skip_roles):
            reason = "setting 'skip_roles' must be a list of role names"
            return make_result(self.id, request, "error", reason, started)
        unknown_roles = sorted(set(skip_roles) - _ROLES)
        if unknown_roles:
            reason = f"setting 'skip_roles' has unknown roles: {', '.join(unknown_roles)}"
            return make_result(self.id, request, "error", reason, started)

        # The whole forwarded conversation, so a block stays final while the agent keeps
        # re-sending the content; normalized once per message, not once per signature.
        # skip_roles exempts history the operator wrote (a defensive system prompt quotes the
        # very attacks it forbids); the model's new reply (REPLY_INDEX) is always scanned.
        texts = [
            form
            for index, text in targets(request, scope="all")
            if index == REPLY_INDEX or request.messages[index].role not in skip_roles
            for form in _forms(text)
        ]
        for sig in active:
            if any(sig.pattern.search(text) for text in texts):
                # The matched text stays out of the reason: it may be the payload or a secret.
                reason = f"signature {sig.id} ({sig.category}): {sig.description}"
                return make_result(self.id, request, "block", reason, started)
        reason = f"no match among {len(active)} signatures"
        return make_result(self.id, request, "allow", reason, started)


CHECK = SignaturesCheck()
