from typing import Any, Protocol


class UpstreamError(Exception):
    """The upstream LLM timed out, was unreachable or returned an error status."""


class Upstream(Protocol):
    """Client for the upstream LLM, an OpenAI-compatible endpoint chosen per model by the policy.

    It speaks vendor JSON, so only the adapter and the proxy service may use it.
    Implementations raise UpstreamError, so the caller can answer `upstream_unavailable`.
    """

    async def chat(self, model: str, payload: dict[str, Any]) -> dict[str, Any]: ...
