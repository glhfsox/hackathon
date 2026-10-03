import logging
import uuid

from app.core.pipeline import Pipeline
from app.models import Action, CanonicalRequest, Checkpoint, Decision, Message
from app.protocols.adapter import InvalidRequestError, ProviderAdapter
from app.protocols.upstream import Upstream, UpstreamError
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.control_trace import ControlTrace

logger = logging.getLogger(__name__)

UPSTREAM_UNAVAILABLE = "upstream_unavailable"


def _request_checkpoint(request: CanonicalRequest) -> Checkpoint:
    """A conversation ending in a tool message is data coming back from a tool."""
    if request.messages and request.messages[-1].role == "tool":
        return Checkpoint.TOOL_RESULT
    return Checkpoint.INPUT


def _reply_checkpoint(reply: Message) -> Checkpoint:
    return Checkpoint.TOOL_CALL if reply.tool_calls else Checkpoint.OUTPUT


class ProxyService:
    """One chat request end to end: adapt, check, forward, check the reply, respond.

    Depends only on protocols and the pipeline. Vendor JSON is touched only through the
    adapter and the upstream client.
    """

    def __init__(self, adapter: ProviderAdapter, pipeline: Pipeline, upstream: Upstream) -> None:
        self._adapter = adapter
        self._pipeline = pipeline
        self._upstream = upstream

    async def handle(
        self, body: ChatCompletionRequest, *, caller_id: str
    ) -> ChatCompletionResponse:
        """Raises InvalidRequestError for a malformed body. The route answers 400."""
        request_id = str(uuid.uuid4())
        request = self._adapter.to_canonical(body, request_id=request_id, caller_id=caller_id)
        request = request.model_copy(update={"checkpoint": _request_checkpoint(request)})
        decisions: list[Decision] = []

        # input or tool_result checkpoint
        decision, request = await self._pipeline.run(request)
        decisions.append(decision)
        if decision.action is Action.BLOCK:
            return self._blocked(body.model, request_id, decisions)

        try:
            payload = self._adapter.to_upstream(request, body)
            upstream_response = await self._upstream.chat(body.model, payload)
            reply = self._adapter.reply_to_canonical(upstream_response)
        except (UpstreamError, InvalidRequestError):
            logger.exception("upstream failed for request %s", request_id)
            return self._upstream_unavailable(body.model, request_id, decisions)

        # tool_call or output checkpoint
        reply_request = request.model_copy(
            update={"checkpoint": _reply_checkpoint(reply), "reply": reply}
        )
        decision, reply_request = await self._pipeline.run(reply_request)
        decisions.append(decision)
        if decision.action is Action.BLOCK:
            return self._blocked(body.model, request_id, decisions)

        # the pipeline may have redacted the reply
        final_reply = reply_request.reply if reply_request.reply is not None else reply
        try:
            return self._adapter.to_response(
                upstream_response,
                final_reply,
                ControlTrace(request_id=request_id, decisions=decisions),
            )
        except InvalidRequestError:
            logger.exception("unusable upstream response for request %s", request_id)
            return self._upstream_unavailable(body.model, request_id, decisions)

    def _blocked(
        self, model: str, request_id: str, decisions: list[Decision]
    ) -> ChatCompletionResponse:
        """Refusal for the last decision, which is the one that blocked."""
        decision = decisions[-1]
        reason = next((r.reason for r in decision.results if r.check == decision.blocked_by), "")
        trace = ControlTrace(request_id=request_id, decisions=decisions)
        return self._adapter.refusal(model, trace, decision.blocked_by or "unknown", reason)

    def _upstream_unavailable(
        self, model: str, request_id: str, decisions: list[Decision]
    ) -> ChatCompletionResponse:
        trace = ControlTrace(request_id=request_id, decisions=decisions)
        reason = "the upstream model did not return a usable response"
        return self._adapter.refusal(model, trace, UPSTREAM_UNAVAILABLE, reason)
