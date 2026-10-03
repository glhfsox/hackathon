import logging
import time
import uuid
from datetime import UTC, datetime
from typing import NoReturn

from app.core.budget import UsageLedger
from app.core.pipeline import Pipeline, detect_reply_checkpoint, detect_request_checkpoint
from app.core.signatures import SignatureFeed
from app.models import (
    Action,
    AuditRecord,
    Decision,
    PolicySnapshot,
)
from app.protocols.adapter import InvalidRequestError, ProviderAdapter
from app.protocols.audit import AuditSink
from app.protocols.policy_provider import PolicyProvider
from app.protocols.upstream import Upstream, UpstreamError
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.control_trace import ControlTrace

logger = logging.getLogger(__name__)

UPSTREAM_UNAVAILABLE = "upstream_unavailable"
BAD_REQUEST = "bad_request"
# The caller_id of every request until caller identity lands (JWT, see AGENTS.md §8). Budgets,
# the audit log and metrics are keyed by it, so they count all traffic as one caller for now.
ANONYMOUS_CALLER = "anonymous"


class ProxyService:
    """One chat request end to end: adapt, check, forward, check the reply, respond.

    Every request takes one policy snapshot at its start and uses it to the end, so a hot reload
    never mixes two policy versions in one request. Depends only on protocols and the pipeline.
    Vendor JSON is touched only through the adapter and the upstream client.
    """

    def __init__(
        self,
        adapter: ProviderAdapter,
        pipeline: Pipeline,
        upstream: Upstream,
        policy: PolicyProvider,
        ledger: UsageLedger,
        audit: AuditSink,
        signatures: SignatureFeed | None = None,
    ) -> None:
        self._adapter = adapter
        self._pipeline = pipeline
        self._upstream = upstream
        self._policy = policy
        self._ledger = ledger
        self._audit = audit
        self._signatures = signatures

    async def handle(self, body: ChatCompletionRequest) -> ChatCompletionResponse:
        """Raises InvalidRequestError (400), audited."""
        snapshot = self._policy.current()
        request_id = str(uuid.uuid4())
        caller_id = ANONYMOUS_CALLER
        try:
            request = self._adapter.to_canonical(body, request_id=request_id, caller_id=caller_id)
        except InvalidRequestError as exc:
            await self._bad_request(snapshot, str(exc), request_id, caller_id)
        request = request.model_copy(
            update={"checkpoint": detect_request_checkpoint(request.messages)}
        )
        await self._refresh_signatures(snapshot)
        model = body.model

        # input or tool_result checkpoint
        decision, forwarded = await self._pipeline.run(request, snapshot)
        decisions = [decision]
        if decision.action is Action.BLOCK:
            return self._blocked(model, request_id, decisions)
        # Counted only once the request is allowed and about to reach the model.
        self._ledger.record_request(caller_id)

        model_cfg = snapshot.policy.models.get(model)
        started = time.perf_counter()
        try:
            if model_cfg is None:
                raise UpstreamError(f"model {model!r} has no upstream in the policy")
            upstream_response = await self._upstream.chat(
                self._adapter.to_upstream(forwarded, body),
                base_url=model_cfg.upstream_base_url,
                timeout_s=model_cfg.timeout_s,
            )
            reply = self._adapter.reply_to_canonical(upstream_response)
            usage = self._adapter.reply_usage(upstream_response)
        except (UpstreamError, InvalidRequestError) as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            # The messages of both errors never quote the upstream body, which may carry PII.
            reason = f"upstream for model {model!r} failed: {exc}"
            logger.warning("request %s (caller %s): %s", request_id, caller_id, reason)
            await self._event(
                snapshot,
                UPSTREAM_UNAVAILABLE,
                reason,
                request_id=request_id,
                caller_id=caller_id,
                model=model,
                latency_ms=latency_ms,
            )
            trace = ControlTrace(request_id=request_id, decisions=decisions)
            return self._adapter.refusal(model, trace, UPSTREAM_UNAVAILABLE, reason)
        usage = usage.model_copy(
            update={"upstream_latency_ms": (time.perf_counter() - started) * 1000.0}
        )
        tokens = usage.prompt_tokens + usage.completion_tokens
        # The tokens are spent whatever the reply checkpoint decides.
        self._ledger.record_usage(caller_id, tokens, tokens / 1000 * model_cfg.price_per_1k_tokens)

        # tool_call or output checkpoint. The agent's own messages, not the redacted copy, so the
        # conversation_id of the audit rows stays stable across the session.
        reply_request = request.model_copy(
            update={"checkpoint": detect_reply_checkpoint(reply), "reply": reply}
        )
        decision, checked = await self._pipeline.run(reply_request, snapshot, usage=usage)
        decisions.append(decision)
        if decision.action is Action.BLOCK:
            return self._blocked(model, request_id, decisions)
        # the pipeline may have redacted the reply
        final_reply = checked.reply if checked.reply is not None else reply
        return self._adapter.to_response(
            upstream_response,
            final_reply,
            ControlTrace(request_id=request_id, decisions=decisions),
            model=model,
            usage=usage,
        )

    async def reject_body(self, detail: str) -> NoReturn:
        """A body the route itself could not parse, audited like one the adapter rejects.

        Raises InvalidRequestError. `detail` must name fields only, never quote the input.
        """
        snapshot = self._policy.current()
        await self._bad_request(snapshot, detail, str(uuid.uuid4()), ANONYMOUS_CALLER)

    async def _bad_request(
        self, snapshot: PolicySnapshot, detail: str, request_id: str, caller_id: str
    ) -> NoReturn:
        reason = f"malformed request body: {detail}"
        await self._event(snapshot, BAD_REQUEST, reason, request_id=request_id, caller_id=caller_id)
        raise InvalidRequestError(reason)

    async def _refresh_signatures(self, snapshot: PolicySnapshot) -> None:
        cfg = snapshot.policy.signatures
        if self._signatures is None or cfg is None:
            return
        # A relative feed source resolves against the policy file's directory.
        base_dir = snapshot.path.parent if snapshot.path is not None else None
        await self._signatures.refresh(cfg, base_dir, self._audit, policy_version=snapshot.version)

    async def _event(
        self,
        snapshot: PolicySnapshot,
        check: str,
        reason: str,
        *,
        request_id: str,
        caller_id: str | None = None,
        model: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Audit a system event (bad_request, upstream_unavailable)."""
        await self._audit.write(
            AuditRecord(
                ts=datetime.now(UTC).isoformat(),
                request_id=request_id,
                caller_id=caller_id,
                model=model,
                check=check,
                action=Action.BLOCK,
                reason=reason,
                latency_ms=latency_ms,
                policy_version=snapshot.version,
            )
        )

    def _blocked(
        self, model: str, request_id: str, decisions: list[Decision]
    ) -> ChatCompletionResponse:
        """Refusal for the last decision, which is the one that blocked."""
        decision = decisions[-1]
        reason = next((r.reason for r in decision.results if r.check == decision.blocked_by), "")
        trace = ControlTrace(request_id=request_id, decisions=decisions)
        return self._adapter.refusal(model, trace, decision.blocked_by or "unknown", reason)
