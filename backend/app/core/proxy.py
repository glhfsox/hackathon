import logging
import time
import uuid
from datetime import UTC, datetime
from typing import NoReturn

from app.checks.base import safe_label
from app.core.auth import InvalidTokenError, JwtAuthenticator
from app.core.pipeline import Pipeline, detect_reply_checkpoint, detect_request_checkpoint
from app.core.rbac import deny_tool_calls, filter_tools
from app.core.signatures import SignatureFeed
from app.models import (
    Action,
    AuditRecord,
    Caller,
    CanonicalRequest,
    Checkpoint,
    Decision,
    Identity,
    Message,
    PolicySnapshot,
)
from app.protocols.adapter import InvalidRequestError, ProviderAdapter
from app.protocols.audit import AuditSink
from app.protocols.policy_provider import PolicyProvider
from app.protocols.upstream import Upstream, UpstreamError
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.control_trace import ControlTrace
from app.schemas.tool_check_request import ToolCheckRequest
from app.schemas.tool_check_response import ToolCheckResponse

logger = logging.getLogger(__name__)

UPSTREAM_UNAVAILABLE = "upstream_unavailable"
AUTH_FAILED = "auth_failed"
BAD_REQUEST = "bad_request"
# AuditRecord.check of the role-based decisions about single tools.
RBAC = "rbac"
# CanonicalRequest.model of a tool-guard request: the guard body in the contract names no model.
GUARD_MODEL = "tool_guard"


class UnauthorizedError(Exception):
    """The token is missing, invalid or expired. The route answers 401."""


def _caller_for(identity: Identity, allowed: frozenset[str]) -> Caller:
    """What the checks need to know about the user: their roles and the tools those roles allow."""
    return Caller(role=", ".join(identity.roles) or "none", allowed_tools=sorted(allowed))


class ProxyService:
    """One chat request end to end: auth, adapt, check, forward, check the reply, respond.

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
        audit: AuditSink,
        authenticator: JwtAuthenticator,
        signatures: SignatureFeed | None = None,
    ) -> None:
        self._adapter = adapter
        self._pipeline = pipeline
        self._upstream = upstream
        self._policy = policy
        self._audit = audit
        self._authenticator = authenticator
        self._signatures = signatures

    async def handle(
        self, body: ChatCompletionRequest, *, token: str | None
    ) -> ChatCompletionResponse:
        """Raises UnauthorizedError (401) or InvalidRequestError (400), both audited."""
        snapshot = self._policy.current()
        request_id = str(uuid.uuid4())
        identity = await self._authenticate(snapshot, token, request_id)
        caller_id = identity.user
        allowed = snapshot.policy.allowed_tools(identity.roles)
        caller = _caller_for(identity, allowed)
        try:
            request = self._adapter.to_canonical(body, request_id=request_id, caller_id=caller_id)
        except InvalidRequestError as exc:
            await self._bad_request(snapshot, str(exc), request_id, caller_id)
        request = request.model_copy(
            update={"checkpoint": detect_request_checkpoint(request.messages)}
        )
        # Inbound: the model is never shown a tool the user may not use.
        request, removed = filter_tools(request, allowed)
        await self._log_tools(
            snapshot,
            request,
            request.checkpoint,
            identity,
            removed,
            allowed=False,
            what="removed from the request",
        )
        await self._refresh_signatures(snapshot)
        model = body.model

        # input or tool_result checkpoint
        decision, forwarded = await self._pipeline.run(request, snapshot, caller)
        decisions = [decision]
        if decision.action is Action.BLOCK:
            return self._blocked(model, request_id, decisions)

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

        # Outbound: a call to a tool the user may not use becomes a text notice. The model can ask
        # for a tool it was never shown, so this runs on every reply.
        reply, denied = deny_tool_calls(reply, allowed)
        await self._log_tools(
            snapshot,
            request,
            Checkpoint.TOOL_CALL,
            identity,
            denied,
            allowed=False,
            what="call replaced by a notice",
        )
        await self._log_tools(
            snapshot,
            request,
            Checkpoint.TOOL_CALL,
            identity,
            [call.name for call in reply.tool_calls],
            allowed=True,
            what="call allowed",
        )

        # tool_call or output checkpoint. The agent's own messages, not the redacted copy, so the
        # conversation_id of the audit rows stays stable across the session.
        reply_request = request.model_copy(
            update={"checkpoint": detect_reply_checkpoint(reply), "reply": reply}
        )
        decision, checked = await self._pipeline.run(reply_request, snapshot, caller, usage=usage)
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

    async def check_tool(self, body: ToolCheckRequest, *, token: str | None) -> ToolCheckResponse:
        """The tool guard: the tool_call checkpoint for one call. Raises UnauthorizedError."""
        snapshot = self._policy.current()
        request_id = str(uuid.uuid4())
        identity = await self._authenticate(snapshot, token, request_id)
        allowed = snapshot.policy.allowed_tools(identity.roles)
        caller = _caller_for(identity, allowed)
        await self._refresh_signatures(snapshot)
        request = CanonicalRequest(
            request_id=request_id,
            caller_id=identity.user,
            model=GUARD_MODEL,
            checkpoint=Checkpoint.TOOL_CALL,
            messages=body.messages,
            reply=Message(role="assistant", tool_calls=[body.tool_call]),
        )
        decision, _ = await self._pipeline.run(request, snapshot, caller)
        return ToolCheckResponse(allowed=decision.action is not Action.BLOCK, decision=decision)

    async def reject_body(self, detail: str, *, token: str | None) -> NoReturn:
        """A body the route itself could not parse, audited like one the adapter rejects.

        Raises UnauthorizedError when the token is invalid (auth comes first, as for any request),
        else InvalidRequestError. `detail` must name fields only, never quote the input.
        """
        snapshot = self._policy.current()
        request_id = str(uuid.uuid4())
        identity = await self._authenticate(snapshot, token, request_id)
        await self._bad_request(snapshot, detail, request_id, identity.user)

    async def _authenticate(
        self, snapshot: PolicySnapshot, token: str | None, request_id: str
    ) -> Identity:
        """`token` is None when there is no Authorization header at all."""
        try:
            if token is None:
                raise InvalidTokenError("missing Authorization header")
            return self._authenticator.authenticate(token)
        except InvalidTokenError as exc:
            # The reason names what is wrong; the token itself is never written anywhere.
            await self._event(snapshot, AUTH_FAILED, str(exc), request_id=request_id)
            raise UnauthorizedError("invalid or missing token") from exc

    async def _log_tools(
        self,
        snapshot: PolicySnapshot,
        request: CanonicalRequest,
        checkpoint: Checkpoint,
        identity: Identity,
        tools: list[str],
        *,
        allowed: bool,
        what: str,
    ) -> None:
        """One audit row per tool decision: user, tool, allowed or denied, and why."""
        roles = ", ".join(repr(safe_label(role)) for role in identity.roles) or "none"
        for tool in tools:
            name = safe_label(tool)
            if allowed:
                reason = f"tool {name!r} {what} for roles {roles}"
            else:
                reason = f"tool {name!r} {what}: not allowed for roles {roles}"
            await self._audit.write(
                AuditRecord(
                    ts=datetime.now(UTC).isoformat(),
                    request_id=request.request_id,
                    caller_id=identity.user,
                    model=request.model,
                    checkpoint=checkpoint,
                    check=RBAC,
                    action=Action.ALLOW if allowed else Action.BLOCK,
                    reason=reason,
                    score=0.0 if allowed else 1.0,
                    policy_version=snapshot.version,
                    tools=name,
                )
            )

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
        """Audit a system event (auth_failed, bad_request, upstream_unavailable)."""
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
