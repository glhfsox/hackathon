"""DEMO STAND-IN for the control-layer proxy. It is not the production proxy.

A teammate is writing the real proxy. Until it lands, this minimal gateway lets the demo agents
run today. It implements the two agent-facing endpoints of contracts/http-api.md exactly, so the
agents move to the real proxy by changing only their base URL:

- POST /v1/chat/completions  OpenAI-compatible: input/tool_result -> upstream -> tool_call/output
- POST /v1/tools/check       the tool guard: one tool call at the tool_call checkpoint

plus GET /api/health and GET /api/metrics so the demo is observable. Nothing else from /api.

Only the HTTP glue and the OpenAI <-> canonical translation live here. The decisions come from the
real pieces: app.pipeline, PolicyStore (hot reload), SignatureFeed, UsageLedger, JevClient, the
JSONL (+ optional Langfuse) audit sinks and StatsExporter (docs/architecture.md §3).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.audit import AuditSink
from app.budget import UsageLedger
from app.checks.base import Signature
from app.jev import JevClient
from app.metrics import compute_metrics
from app.models import (
    Action,
    AuditRecord,
    CanonicalRequest,
    Checkpoint,
    Decision,
    Message,
    ToolCall,
    ToolDef,
    Usage,
)
from app.pipeline import detect_reply_checkpoint, detect_request_checkpoint, run_checkpoint
from app.policy import Caller
from app.policy_store import LoadedPolicy, PolicyStore
from app.signatures import SignatureFeed
from app.sinks import FanoutAuditSink, JsonlAuditSink, read_jsonl
from app.stats import StatsExporter
from app.tracing import build_langfuse_sink

log = logging.getLogger(__name__)

# The policy has no upstream timeout yet, and a local model can take tens of seconds per reply.
UPSTREAM_TIMEOUT_S = 180.0
# CanonicalRequest.model of a tool-guard request: the guard body in the contract names no model.
GUARD_MODEL = "tool_guard"
UPSTREAM_UNAVAILABLE = "upstream_unavailable"


# --- OpenAI wire shapes (validated at the boundary; vendor JSON stops here) -------------------


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _WireFunction(_Wire):
    name: str
    arguments: str = ""  # a JSON string on the wire, a dict in the canonical model


class _WireToolCall(_Wire):
    id: str
    type: Literal["function"] = "function"
    function: _WireFunction


class _WireMessage(_Wire):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[_WireToolCall] | None = None
    tool_call_id: str | None = None


class _WireToolSpec(_Wire):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class _WireTool(_Wire):
    type: Literal["function"] = "function"
    function: _WireToolSpec


class _ChatRequest(_Wire):
    model: str
    messages: list[_WireMessage] = Field(min_length=1)
    tools: list[_WireTool] | None = None


class _WireUsage(_Wire):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class _UpstreamChoice(_Wire):
    message: _WireMessage
    finish_reason: str | None = None


class _UpstreamResponse(_Wire):
    id: str | None = None
    created: int | None = None
    choices: list[_UpstreamChoice] = Field(min_length=1)
    usage: _WireUsage | None = None


class _ToolCheckRequest(_Wire):
    tool_call: ToolCall
    messages: list[Message]


def _arguments(raw: str) -> dict[str, Any]:
    value = json.loads(raw) if raw.strip() else {}
    if not isinstance(value, dict):
        raise ValueError("tool call arguments must be a JSON object")
    return value


def _to_canonical(m: _WireMessage) -> Message:
    calls = [
        ToolCall(id=c.id, name=c.function.name, arguments=_arguments(c.function.arguments))
        for c in m.tool_calls or []
    ]
    return Message(role=m.role, content=m.content, tool_calls=calls, tool_call_id=m.tool_call_id)


def _forward_body(
    body: dict[str, Any], original: CanonicalRequest, checked: CanonicalRequest
) -> dict[str, Any]:
    """The agent's own body with the redacted contents swapped in and streaming off.

    Redactions only ever change message contents, so every other field (and every untouched
    message) goes upstream exactly as the agent sent it.
    """
    out = {k: v for k, v in body.items() if k != "stream_options"}  # only valid with stream: true
    out["stream"] = False
    wire = [dict(m) for m in body["messages"]]
    for i, (before, after) in enumerate(zip(original.messages, checked.messages, strict=True)):
        if after.content != before.content:
            wire[i]["content"] = after.content
    out["messages"] = wire
    return out


# --- responses -----------------------------------------------------------------------------


def _control(request_id: str, decisions: list[Decision]) -> dict[str, Any]:
    return {"request_id": request_id, "decisions": [d.model_dump(mode="json") for d in decisions]}


def _refusal(
    request_id: str, model: str, check: str, reason: str, decisions: list[Decision]
) -> JSONResponse:
    """The blocked shape of contracts/http-api.md: HTTP 200, so the agent keeps running."""
    return JSONResponse(
        {
            "id": f"ctl-{request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": f"Blocked by {check}: {reason}"},
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "control": _control(request_id, decisions),
        }
    )


def _blocked(request_id: str, model: str, decisions: list[Decision]) -> JSONResponse:
    decision = decisions[-1]
    # A block stops the pipeline, so the blocking result is the last one.
    return _refusal(
        request_id, model, decision.blocked_by or "", decision.results[-1].reason, decisions
    )


def _completion(
    up: _UpstreamResponse,
    request_id: str,
    model: str,
    reply: Message,
    usage: Usage,
    decisions: list[Decision],
) -> dict[str, Any]:
    """Only the fields the checks inspected go back: e.g. Ollama's `reasoning` is dropped."""
    message: dict[str, Any] = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in reply.tool_calls
        ]
    finish = up.choices[0].finish_reason or ("tool_calls" if reply.tool_calls else "stop")
    return {
        "id": up.id or f"ctl-{request_id}",
        "object": "chat.completion",
        "created": up.created or int(time.time()),
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.prompt_tokens + usage.completion_tokens,
        },
        "control": _control(request_id, decisions),
    }


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def _describe(exc: Exception) -> str:
    """For audit reasons. A pydantic error gives field paths and messages only: its str() would
    quote the input, which may be PII. The other errors here (JSON decoding, httpx, our own)
    never quote a body; the type name keeps a timeout, whose str() is "", readable."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in exc.errors()
        )
    return f"{type(exc).__name__}: {exc}"


# --- the gateway ---------------------------------------------------------------------------


@dataclass
class _Gateway:
    store: PolicyStore
    audit: AuditSink
    feed: SignatureFeed
    ledger: UsageLedger
    judge: JevClient
    http: httpx.AsyncClient
    logs_dir: Path

    async def event(
        self,
        loaded: LoadedPolicy,
        check: str,
        reason: str,
        *,
        request_id: str,
        caller_id: str | None = None,
        model: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Audit a system event (auth_failed, bad_request, upstream_unavailable)."""
        await self.audit.write(
            AuditRecord(
                ts=datetime.now(UTC).isoformat(),
                request_id=request_id,
                caller_id=caller_id,
                model=model,
                check=check,
                action=Action.block,
                reason=reason,
                latency_ms=latency_ms,
                policy_version=loaded.version,
            )
        )

    async def authenticate(
        self, loaded: LoadedPolicy, request: Request, request_id: str
    ) -> tuple[str, Caller] | JSONResponse:
        header = request.headers.get("authorization", "")
        scheme, _, key = header.partition(" ")
        key = key.strip()
        found = None
        if scheme.lower() == "bearer" and key:
            found = loaded.policy.caller_for_key(key)
        if found is None:
            # The key itself is never written anywhere.
            reason = "unknown API key" if header else "missing Authorization header"
            await self.event(loaded, "auth_failed", reason, request_id=request_id)
            return _error(401, "invalid or missing API key", "authentication_error")
        return found

    async def bad_request(
        self, loaded: LoadedPolicy, exc: ValueError, *, request_id: str, caller_id: str
    ) -> JSONResponse:
        reason = f"malformed request body: {_describe(exc)}"
        await self.event(loaded, "bad_request", reason, request_id=request_id, caller_id=caller_id)
        return _error(400, reason, "invalid_request_error")

    async def signatures(self, loaded: LoadedPolicy) -> list[Signature] | None:
        cfg = loaded.policy.signatures
        if cfg is None:
            # No feed configured: the signatures check, if on, fails closed on None.
            return None
        # A relative source resolves against the policy file's directory.
        await self.feed.refresh(cfg, loaded.path.parent, self.audit, policy_version=loaded.version)
        return self.feed.current()

    async def chat(self, request: Request) -> JSONResponse:
        loaded = self.store.current()  # one policy snapshot for the whole request
        policy = loaded.policy
        request_id = str(uuid.uuid4())
        who = await self.authenticate(loaded, request, request_id)
        if isinstance(who, JSONResponse):
            return who
        caller_id, caller = who
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("the body must be a JSON object")
            parsed = _ChatRequest.model_validate(body)
            messages = [_to_canonical(m) for m in parsed.messages]
        except ValueError as exc:  # bad JSON, a ValidationError, or tool arguments not JSON
            return await self.bad_request(loaded, exc, request_id=request_id, caller_id=caller_id)

        model = parsed.model
        canonical = CanonicalRequest(
            request_id=request_id,
            caller_id=caller_id,
            model=model,
            checkpoint=detect_request_checkpoint(messages),
            messages=messages,
            tools=[
                ToolDef(
                    name=t.function.name,
                    description=t.function.description,
                    parameters=t.function.parameters,
                )
                for t in parsed.tools or []
            ],
        )
        shared = {
            "ledger": self.ledger,
            "signatures": await self.signatures(loaded),
            "judge": self.judge,
            "audit": self.audit,
        }
        decision, checked = await run_checkpoint(
            canonical, policy, loaded.version, caller, **shared
        )
        decisions = [decision]
        if decision.action == Action.block:
            return _blocked(request_id, model, decisions)
        # Counted only once the request is allowed and about to reach the model.
        self.ledger.record_request(caller_id)

        model_cfg = policy.models.get(model)
        started = time.perf_counter()
        try:
            if model_cfg is None:
                raise ValueError(f"model {model!r} has no upstream in the policy")
            url = f"{model_cfg.upstream_base_url.rstrip('/')}/chat/completions"
            resp = await self.http.post(
                url, json=_forward_body(body, canonical, checked), timeout=UPSTREAM_TIMEOUT_S
            )
            resp.raise_for_status()
            up = _UpstreamResponse.model_validate(resp.json())
            reply = _to_canonical(up.choices[0].message).model_copy(update={"role": "assistant"})
        except (httpx.HTTPError, ValueError) as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            reason = f"upstream for model {model!r} failed: {_describe(exc)}"
            log.warning("request %s (caller %s): %s", request_id, caller_id, reason)
            await self.event(
                loaded,
                UPSTREAM_UNAVAILABLE,
                reason,
                request_id=request_id,
                caller_id=caller_id,
                model=model,
                latency_ms=latency_ms,
            )
            return _refusal(request_id, model, UPSTREAM_UNAVAILABLE, reason, decisions)
        latency_ms = (time.perf_counter() - started) * 1000.0
        up_usage = up.usage or _WireUsage()
        usage = Usage(
            prompt_tokens=up_usage.prompt_tokens,
            completion_tokens=up_usage.completion_tokens,
            upstream_latency_ms=latency_ms,
        )
        tokens = usage.prompt_tokens + usage.completion_tokens
        # The tokens are spent whatever the reply checkpoint decides.
        self.ledger.record_usage(caller_id, tokens, tokens / 1000 * model_cfg.price_per_1k_tokens)

        # The agent's own messages, not the redacted copy, so the conversation_id stays stable.
        reply_request = canonical.model_copy(
            update={"checkpoint": detect_reply_checkpoint(reply), "reply": reply}
        )
        reply_decision, reply_checked = await run_checkpoint(
            reply_request, policy, loaded.version, caller, usage=usage, **shared
        )
        decisions.append(reply_decision)
        if reply_decision.action == Action.block:
            return _blocked(request_id, model, decisions)
        assert reply_checked.reply is not None  # set above; the pipeline keeps it
        return JSONResponse(
            _completion(up, request_id, model, reply_checked.reply, usage, decisions)
        )

    async def tools_check(self, request: Request) -> JSONResponse:
        loaded = self.store.current()
        request_id = str(uuid.uuid4())
        who = await self.authenticate(loaded, request, request_id)
        if isinstance(who, JSONResponse):
            return who
        caller_id, caller = who
        try:
            body = await request.json()
            parsed = _ToolCheckRequest.model_validate(body)
        except ValueError as exc:
            return await self.bad_request(loaded, exc, request_id=request_id, caller_id=caller_id)
        canonical = CanonicalRequest(
            request_id=request_id,
            caller_id=caller_id,
            model=GUARD_MODEL,
            checkpoint=Checkpoint.tool_call,
            messages=parsed.messages,
            reply=Message(role="assistant", tool_calls=[parsed.tool_call]),
        )
        decision, _ = await run_checkpoint(
            canonical,
            loaded.policy,
            loaded.version,
            caller,
            ledger=self.ledger,
            signatures=await self.signatures(loaded),
            judge=self.judge,
            audit=self.audit,
        )
        return JSONResponse(
            {
                "allowed": decision.action != Action.block,
                "decision": decision.model_dump(mode="json"),
            }
        )

    async def metrics(self, since: str | None) -> JSONResponse:
        loaded = self.store.current()
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            # The stand-in covers the current UTC day only.
            records = await asyncio.to_thread(read_jsonl, self.logs_dir, day_start.isoformat())
            return JSONResponse(compute_metrics(records, loaded.policy, since=since))
        except ValueError as exc:  # `since` is not ISO 8601
            return _error(400, str(exc), "invalid_request_error")


def create_app(
    policy_path: Path, logs_dir: Path, *, http_client: httpx.AsyncClient | None = None
) -> FastAPI:
    """The demo gateway. `http_client` (upstream and Jev calls) is owned by the caller if given."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http = http_client or httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_S)
        langfuse = build_langfuse_sink()
        try:
            audit = FanoutAuditSink([JsonlAuditSink(logs_dir), *([langfuse] if langfuse else [])])
            store = PolicyStore(policy_path, audit)
            await store.load_initial()
            exporter = StatsExporter(logs_dir, lambda: store.current().policy)
            app.state.gateway = _Gateway(
                store=store,
                audit=audit,
                feed=SignatureFeed(),
                ledger=UsageLedger(),
                judge=JevClient(lambda: store.current().policy.jev, http),
                http=http,
                logs_dir=logs_dir,
            )
            store.start()
            exporter.start()
            try:
                yield
            finally:
                await exporter.stop()
                await store.stop()
        finally:
            if http_client is None:
                await http.aclose()
            if langfuse is not None:
                # Sends what is still queued; blocks on the network.
                await asyncio.to_thread(langfuse.shutdown)

    app = FastAPI(title="AI Control Layer (demo gateway stand-in)", lifespan=lifespan)

    def gateway(request: Request) -> _Gateway:
        return request.app.state.gateway

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        return await gateway(request).chat(request)

    @app.post("/v1/tools/check")
    async def tools_check(request: Request) -> JSONResponse:
        return await gateway(request).tools_check(request)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, str]:
        gw = gateway(request)
        return {
            "status": "ok",
            "policy_version": gw.store.current().version,
            **(await gw.judge.health()),
        }

    @app.get("/api/metrics")
    async def metrics(request: Request, since: str | None = None) -> JSONResponse:
        return await gateway(request).metrics(since)

    return app
