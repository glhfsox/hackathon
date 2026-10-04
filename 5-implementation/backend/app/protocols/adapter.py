from typing import Any, Protocol

from app.models import CanonicalRequest, Message, Usage
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.control_trace import ControlTrace


class InvalidRequestError(Exception):
    """Raised by an adapter when the body cannot be parsed. The route answers 400."""


class ProviderAdapter(Protocol):
    """Translates between one vendor's JSON and the canonical models.

    The only code that knows the vendor shape. Messages keep their index across the
    translation, so Redaction.message_index stays valid.
    """

    def to_canonical(
        self, body: ChatCompletionRequest, *, request_id: str, caller_id: str
    ) -> CanonicalRequest:
        """Vendor request to canonical. Raises InvalidRequestError on a malformed body."""
        ...

    def to_upstream(
        self, request: CanonicalRequest, original: ChatCompletionRequest
    ) -> dict[str, Any]:
        """Canonical (with redactions applied) back to a vendor payload.

        Starts from the original body so parameters the layer does not model are kept. The tools
        come from the canonical request: a tool removed from it is removed from the payload too.
        """
        ...

    def reply_to_canonical(self, upstream_response: dict[str, Any]) -> Message:
        """Extract the model's reply from the upstream response. Raises InvalidRequestError."""
        ...

    def reply_usage(self, upstream_response: dict[str, Any]) -> Usage:
        """Token usage of the upstream call (no latency). Raises InvalidRequestError."""
        ...

    def to_response(
        self,
        upstream_response: dict[str, Any],
        reply: Message,
        trace: ControlTrace,
        *,
        model: str,
        usage: Usage,
    ) -> ChatCompletionResponse:
        """A response built from the checked `reply` (possibly redacted) with `control` added.

        Upstream fields no check saw (other choices, vendor extras) are not passed on.
        """
        ...

    def refusal(
        self, model: str, trace: ControlTrace, blocked_by: str, reason: str
    ) -> ChatCompletionResponse:
        """A normal-looking chat response that explains the block. HTTP 200, no upstream call."""
        ...
