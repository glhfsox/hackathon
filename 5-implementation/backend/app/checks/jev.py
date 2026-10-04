"""AI decision: Jev scores what the rule checks let through (docs/architecture.md §4-§5).

Always last (cost_rank 7), so it only sees text the pipeline has already redacted.

Every message of the forwarded conversation is judged on its own, cut into chunks of `max_chars`,
so no text goes unjudged however long it is. A message yields the same JudgeInputs at every step
of a session, which lets the Jev client answer history from its cache instead of paying again.

`max_judge_calls` limits the NEW work of one request, not the length of the conversation:
identical chunks are judged once, and chunks the judge has already answered (its `cached()`,
when it has one) neither count nor wait. The calls run at most `max_concurrency` at a time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.checks.base import REPLY_INDEX, CheckContext, JudgeUnavailable, make_result, targets
from app.models import (
    CanonicalRequest,
    Checkpoint,
    CheckResult,
    DecidedBy,
    JudgeInput,
    JudgeVerdict,
)

log = logging.getLogger(__name__)

# Where a message's text was captured. It follows the role, not the checkpoint of the request
# that carries the message, so a message is judged the same way at every step.
_CAPTURED_AT = {
    "system": Checkpoint.INPUT,
    "user": Checkpoint.INPUT,
    "assistant": Checkpoint.OUTPUT,
    "tool": Checkpoint.TOOL_RESULT,
}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _key(inp: JudgeInput) -> tuple[str, str, str]:
    return (inp.checkpoint.value, inp.text, inp.context)


class JevCheck:
    id = "jev"
    cost_rank = 7
    checkpoints = frozenset(
        {Checkpoint.INPUT, Checkpoint.TOOL_CALL, Checkpoint.TOOL_RESULT, Checkpoint.OUTPUT}
    )

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        skip_roles = settings.get("skip_roles", [])
        if not isinstance(skip_roles, list) or set(map(str, skip_roles)) - _CAPTURED_AT.keys():
            return make_result(self.id, request, "error", "invalid skip_roles in policy", started)
        # A skipped role is one pii_secrets does not redact (the policy enforces that pairing),
        # so it must not be sent to Jev. The model's new reply is always judged.
        texts = [
            (index, text)
            for index, text in targets(request, scope="all")
            if index == REPLY_INDEX or request.messages[index].role not in skip_roles
        ]
        if not texts:
            return make_result(self.id, request, "allow", "nothing to judge", started)
        max_chars = settings.get("max_chars", 4000)
        max_calls = settings.get("max_judge_calls", 128)
        max_concurrency = settings.get("max_concurrency", 8)
        for name, value in (
            ("max_chars", max_chars),
            ("max_judge_calls", max_calls),
            ("max_concurrency", max_concurrency),
        ):
            if not _positive_int(value):
                return make_result(
                    self.id, request, "error", f"invalid {name} {value!r} in policy", started
                )
        judge = ctx.judge
        if judge is None:
            return make_result(
                self.id, request, "error", "no AI decision maker configured", started
            )

        tools = ", ".join(t.name for t in request.tools) or "none"
        inputs: list[JudgeInput] = []
        for index, text in texts:
            role = "assistant" if index == REPLY_INDEX else request.messages[index].role
            context = f"message role: {role}; offered tools: {tools}"
            # At tool_call the text is the tool-call view (task, reasoning, calls), not a message.
            captured = (
                Checkpoint.TOOL_CALL
                if index == REPLY_INDEX and request.checkpoint == Checkpoint.TOOL_CALL
                else _CAPTURED_AT[role]
            )
            inputs += [
                JudgeInput(checkpoint=captured, text=text[i : i + max_chars], context=context)
                for i in range(0, len(text), max_chars)
            ]
        # A repeated chunk (two identical tool results, say) is judged once.
        distinct = list({_key(inp): inp for inp in inputs}.values())
        # History was answered on an earlier step: it costs nothing, so it is not counted.
        lookup = getattr(judge, "cached", None)
        known: dict[tuple[str, str, str], JudgeVerdict] = {}
        new: list[JudgeInput] = []
        for inp in distinct:
            hit = lookup(inp) if callable(lookup) else None
            if hit is None:
                new.append(inp)
            else:
                known[_key(inp)] = hit
        if len(new) > max_calls:
            # Judging only some chunks would let the rest through unseen, so this fails closed.
            reason = (
                f"conversation too large to judge: {len(new)} new chunks exceed "
                f"max_judge_calls {max_calls}"
            )
            return make_result(self.id, request, "error", reason, started)

        gate = asyncio.Semaphore(max_concurrency)

        async def judge_one(inp: JudgeInput) -> JudgeVerdict:
            async with gate:
                return await judge.judge(inp)

        answers = await asyncio.gather(*(judge_one(inp) for inp in new), return_exceptions=True)
        for inp, answer in zip(new, answers, strict=True):
            if isinstance(answer, JudgeUnavailable):
                log.warning(
                    "jev check failing closed for request %s: %s", request.request_id, answer
                )
                return make_result(
                    self.id, request, "error", f"AI decision maker unavailable: {answer}", started
                )
            if isinstance(answer, BaseException):
                # Not an outage but a bug: the pipeline turns it into an error verdict.
                raise answer
            known[_key(inp)] = answer

        # The riskiest chunk decides, with its own reason and decision maker.
        verdict = max((known[_key(inp)] for inp in distinct), key=lambda v: v.score)
        decided_by = DecidedBy(verdict.decided_by)
        if verdict.score >= ctx.jev_threshold:
            cats = f" [{', '.join(verdict.categories)}]" if verdict.categories else ""
            return make_result(
                self.id,
                request,
                "block",
                f"{verdict.reason}{cats}",
                started,
                score=verdict.score,
                decided_by=decided_by,
            )
        return make_result(
            self.id,
            request,
            "allow",
            verdict.reason,
            started,
            score=verdict.score,
            decided_by=decided_by,
        )


CHECK = JevCheck()
