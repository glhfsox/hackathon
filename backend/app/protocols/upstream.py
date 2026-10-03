from typing import Any, Protocol


class Upstream(Protocol):
    """Client for the upstream LLM, an OpenAI-compatible endpoint chosen per model by the policy.

    It speaks vendor JSON, so only the adapter and the proxy route may use it.
    Raises on timeout or transport error, so the caller can answer `upstream_unavailable`.
    """

    async def chat(self, model: str, payload: dict[str, Any]) -> dict[str, Any]: ...
