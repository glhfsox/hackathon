import json
import time
from typing import Any

from pydantic import ValidationError

from app.models import CanonicalRequest, Checkpoint, Message, ToolCall, ToolDef, Usage
from app.protocols.adapter import InvalidRequestError
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.control_trace import ControlTrace


def _describe(exc: ValidationError) -> str:
    """Field paths and messages only: str(exc) would quote the input, which may be PII."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in exc.errors()
    )


def _content_text(raw: Any) -> str | None:
    """OpenAI content is a string, null, or a list of parts. Only text parts are kept."""
    if raw is None or isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(
            part.get("text", "")
            for part in raw
            if isinstance(part, dict) and part.get("type") == "text"
        )
    raise InvalidRequestError(f"unsupported message content: {type(raw).__name__}")


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    function = raw.get("function")
    if not isinstance(function, dict):
        raise InvalidRequestError("tool call has no function")
    try:
        # OpenAI sends the arguments as a JSON string
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidRequestError(f"tool call arguments are not valid JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise InvalidRequestError("tool call arguments must be a JSON object")
    try:
        return ToolCall(id=raw.get("id", ""), name=function.get("name", ""), arguments=arguments)
    except ValidationError as exc:
        raise InvalidRequestError(f"invalid tool call: {_describe(exc)}") from exc


def _parse_message(raw: dict[str, Any]) -> Message:
    role = raw.get("role")
    if role == "developer":  # newer OpenAI name for the system role
        role = "system"
    try:
        return Message(
            role=role,  # validated by pydantic
            content=_content_text(raw.get("content")),
            tool_calls=[_parse_tool_call(call) for call in raw.get("tool_calls") or []],
            tool_call_id=raw.get("tool_call_id"),
        )
    except ValidationError as exc:
        raise InvalidRequestError(f"invalid message: {_describe(exc)}") from exc


def _parse_tool_def(raw: dict[str, Any]) -> ToolDef:
    function = raw.get("function")
    if not isinstance(function, dict):
        raise InvalidRequestError("tool definition has no function")
    try:
        return ToolDef(
            name=function.get("name", ""),
            description=function.get("description", ""),
            parameters=function.get("parameters") or {},
        )
    except ValidationError as exc:
        raise InvalidRequestError(f"invalid tool definition: {_describe(exc)}") from exc


def _first_choice(upstream_response: dict[str, Any]) -> dict[str, Any]:
    choices = upstream_response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise InvalidRequestError("upstream response has no choices")
    return choices[0]


class OpenAIAdapter:
    """The only code that knows the OpenAI chat-completions JSON shape."""

    def to_canonical(
        self, body: ChatCompletionRequest, *, request_id: str, caller_id: str
    ) -> CanonicalRequest:
        return CanonicalRequest(
            request_id=request_id,
            caller_id=caller_id,
            model=body.model,
            # Placeholder: ProxyService detects the real checkpoint from the messages
            checkpoint=Checkpoint.INPUT,
            messages=[_parse_message(raw) for raw in body.messages],
            tools=[_parse_tool_def(raw) for raw in body.tools or []],
        )

    def to_upstream(
        self, request: CanonicalRequest, original: ChatCompletionRequest
    ) -> dict[str, Any]:
        payload = original.model_dump(exclude_unset=True)
        messages: list[dict[str, Any]] = []
        # Same length and order as the original, so message indexes line up
        for raw, message in zip(original.messages, request.messages, strict=True):
            out = dict(raw)
            # Only content can be redacted, so write it back only when it changed
            if message.content != _content_text(raw.get("content")):
                out["content"] = message.content
            messages.append(out)
        payload["messages"] = messages
        payload["stream"] = False  # streaming is accepted but always answered whole
        payload.pop("stream_options", None)  # only valid together with stream: true
        return payload

    def reply_to_canonical(self, upstream_response: dict[str, Any]) -> Message:
        message = _first_choice(upstream_response).get("message")
        if not isinstance(message, dict):
            raise InvalidRequestError("upstream choice has no message")
        # The role is validated, then normalised: the reply is the assistant's whatever it says.
        return _parse_message(message).model_copy(update={"role": "assistant"})

    def reply_usage(self, upstream_response: dict[str, Any]) -> Usage:
        raw = upstream_response.get("usage")
        if raw is None:
            return Usage()
        if not isinstance(raw, dict):
            raise InvalidRequestError("upstream usage is not an object")
        keys = ("prompt_tokens", "completion_tokens")
        try:
            return Usage.model_validate({k: raw[k] for k in keys if k in raw})
        except ValidationError as exc:
            raise InvalidRequestError(f"invalid upstream usage: {_describe(exc)}") from exc

    def to_response(
        self,
        upstream_response: dict[str, Any],
        reply: Message,
        trace: ControlTrace,
        *,
        model: str,
        usage: Usage,
    ) -> ChatCompletionResponse:
        # Only what the checks inspected goes back: other choices and vendor extras such as
        # Ollama's `reasoning` were never checked, so they are dropped.
        message: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in reply.tool_calls
            ]
        finish = _first_choice(upstream_response).get("finish_reason")
        if not isinstance(finish, str):
            finish = "tool_calls" if reply.tool_calls else "stop"
        upstream_id = upstream_response.get("id")
        created = upstream_response.get("created")
        return ChatCompletionResponse.model_validate(
            {
                "id": upstream_id if isinstance(upstream_id, str) else f"ctl-{trace.request_id}",
                "object": "chat.completion",
                "created": created if isinstance(created, int) else int(time.time()),
                "model": model,
                "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                "usage": {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.prompt_tokens + usage.completion_tokens,
                },
                "control": trace,
            }
        )

    def refusal(
        self, model: str, trace: ControlTrace, blocked_by: str, reason: str
    ) -> ChatCompletionResponse:
        return ChatCompletionResponse.model_validate(
            {
                "id": f"ctl-{trace.request_id}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": f"Blocked by {blocked_by}: {reason}",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "control": trace,
            }
        )
