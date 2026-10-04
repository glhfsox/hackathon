"""Public, bounded provider transport. Credentials never leave the function."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal, Protocol

import functions_framework
import httpx
from flask import Request, Response
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

log = logging.getLogger(__name__)


class Settings(BaseModel):
    enabled: bool = False
    expires_at: datetime
    total_calls: int = Field(ge=0)
    calls_per_minute: int = Field(ge=0)
    max_body_bytes: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    openai_model: str
    typesafe_model: str
    upstream_timeout_s: float = Field(gt=0)
    project_id: str
    database: str

    @model_validator(mode="after")
    def aware_expiry(self) -> Settings:
        if self.expires_at.tzinfo is None:
            raise ValueError("expiry must include a timezone")
        return self

    @classmethod
    def from_env(cls) -> Settings:
        return cls.model_validate(json.loads(os.environ["RELAY_SETTINGS"]))


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    response_format: dict[str, Any] | None = None
    stream: Literal[False] = False
    n: Literal[1] = 1
    parallel_tool_calls: bool | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    user: str | None = None


class State(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checkpoint: Literal["input", "tool_call", "tool_result", "output"]
    text: str
    context: str


class RiskQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["noul"]
    instructions: str
    criteria: dict[str, str]

    @model_validator(mode="after")
    def binary_criteria(self) -> RiskQuestion:
        if set(self.criteria) != {"true", "false"}:
            raise ValueError("Risk criteria must be binary")
        return self


class CategoryQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["choice"]
    instructions: str
    criteria: dict[str, str] = Field(min_length=1, max_length=7)


class Questions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    risky: RiskQuestion
    category: CategoryQuestion


class SystemOneBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str
    state: State
    questions: Questions


class Rejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def active(settings: Settings, now: float) -> None:
    if not settings.enabled or not settings.total_calls or not settings.calls_per_minute:
        raise Rejected(503, "Relay disabled")
    if now >= settings.expires_at.timestamp():
        raise Rejected(410, "Judging access expired")


def reserve_state(state: dict[str, Any], settings: Settings, now: float) -> dict[str, int]:
    """Pure transition, called inside the retryable database transaction."""
    active(settings, now)
    total = int(state.get("total_calls", 0))
    minute = int(now // 60)
    count = int(state.get("minute_calls", 0)) if state.get("minute") == minute else 0
    if total >= settings.total_calls:
        raise Rejected(429, "Global call allowance exhausted")
    if count >= settings.calls_per_minute:
        raise Rejected(429, "Global minute allowance exhausted")
    return {"total_calls": total + 1, "minute": minute, "minute_calls": count + 1}


class Quota(Protocol):
    def reserve(self, settings: Settings) -> None: ...


class FirestoreQuota:
    def __init__(self, settings: Settings) -> None:
        self.client = firestore.Client(project=settings.project_id, database=settings.database)
        self.document = self.client.collection("relay").document("usage")

    def reserve(self, settings: Settings) -> None:
        @firestore.transactional
        def commit(transaction: firestore.Transaction) -> None:
            snapshot = self.document.get(transaction=transaction)
            state = snapshot.to_dict() or {}
            # Refresh the clock on each transaction retry; expiry cannot be bypassed by contention.
            transaction.set(self.document, reserve_state(state, settings, time.time()))

        commit(self.client.transaction())


def validate_body(path: str, raw: bytes, settings: Settings) -> tuple[str, dict[str, Any]]:
    if path == "/openai/v1/chat/completions":
        body = ChatBody.model_validate_json(raw)
        if body.model != settings.openai_model:
            raise Rejected(400, "Model not allowed")
        if body.max_tokens is not None and body.max_completion_tokens is not None:
            raise Rejected(400, "Use only one output token limit")
        payload = body.model_dump(exclude_none=True)
        limit_name = "max_completion_tokens" if body.max_completion_tokens else "max_tokens"
        requested = payload.get(limit_name, settings.max_output_tokens)
        if requested > settings.max_output_tokens:
            raise Rejected(400, "Output token limit exceeded")
        payload[limit_name] = requested
        return "openai", payload
    body = SystemOneBody.model_validate_json(raw)
    if body.model != settings.typesafe_model:
        raise Rejected(400, "Model not allowed")
    return "typesafe", body.model_dump()


class Relay:
    def __init__(self, settings: Settings, quota: Quota, http: httpx.Client) -> None:
        self.settings, self.quota, self.http = settings, quota, http

    def handle(self, request: Request) -> Response:
        try:
            return self._handle(request)
        except Rejected as exc:
            return error(exc.status, exc.message)
        except (ValidationError, ValueError):
            # Validation errors contain input values; never return or log them.
            return error(400, "Invalid request body")
        except (GoogleAPICallError, RetryError) as exc:
            log.error("Quota storage unavailable (%s)", type(exc).__name__)
            return error(503, "Quota storage unavailable")

    def _handle(self, request: Request) -> Response:
        settings = self.settings
        active(settings, time.time())
        if request.query_string:
            raise Rejected(400, "Query parameters are not supported")
        if request.path == "/health" and request.method == "GET":
            return json_response({"status": "ok", "expires_at": settings.expires_at.isoformat()})
        if request.path == "/openai/v1/models" and request.method == "GET":
            return json_response(
                {
                    "object": "list",
                    "data": [{"id": settings.openai_model, "object": "model"}],
                }
            )
        if request.path not in (
            "/openai/v1/chat/completions",
            "/typesafe/v1/systemone",
        ):
            raise Rejected(404, "Route not found")
        if request.method != "POST":
            raise Rejected(405, "POST required")
        if request.content_length and request.content_length > settings.max_body_bytes:
            raise Rejected(413, "Request body too large")
        if not request.is_json or request.content_encoding:
            raise Rejected(400, "Uncompressed JSON required")
        # Bound reads even without a Content-Length header.
        raw = request.stream.read(settings.max_body_bytes + 1)
        if len(raw) > settings.max_body_bytes:
            raise Rejected(413, "Request body too large")
        provider, payload = validate_body(request.path, raw, settings)
        key = os.environ.get("OPENAI_API_KEY" if provider == "openai" else "TYPESAFE_API_KEY")
        if not key:
            raise Rejected(503, "Provider credential unavailable")
        try:
            self.quota.reserve(settings)
        except (GoogleAPICallError, RetryError, ValueError) as exc:
            log.error("Quota reservation failed (%s)", type(exc).__name__)
            raise Rejected(503, "Quota storage unavailable") from exc
        url = (
            "https://api.openai.com/v1/chat/completions"
            if provider == "openai"
            else "https://api.typesafe.ai/v1/systemone"
        )
        try:
            response = self.http.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
                timeout=settings.upstream_timeout_s,
                follow_redirects=False,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise Rejected(502, "Provider request failed")
            encoded = json.dumps(body)
            # Defense against accidental credential reflection by an upstream response.
            for secret_name in ("OPENAI_API_KEY", "TYPESAFE_API_KEY"):
                secret = os.environ.get(secret_name)
                if secret and secret in encoded:
                    raise ValueError("Credential reflection")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Provider failed (%s, %s)", provider, type(exc).__name__)
            raise Rejected(502, "Provider request failed") from exc
        return Response(encoded, content_type="application/json")


def json_response(body: dict[str, Any], status: int = 200) -> Response:
    return Response(json.dumps(body), status=status, content_type="application/json")


def error(status: int, message: str) -> Response:
    return json_response({"error": {"message": message, "type": "relay_error"}}, status)


@lru_cache(maxsize=1)
def runtime() -> Relay:
    settings = Settings.from_env()
    return Relay(settings, FirestoreQuota(settings), httpx.Client())


@functions_framework.http
def relay(request: Request) -> Response:
    try:
        return runtime().handle(request)
    except (KeyError, ValidationError, ValueError, GoogleAPICallError) as exc:
        log.error("Relay configuration unavailable (%s)", type(exc).__name__)
        return error(503, "Relay configuration unavailable")
