from typing import Any, Protocol


class UpstreamError(Exception):
    """The upstream LLM timed out, was unreachable or returned an error status."""


class Upstream(Protocol):
    """Client for the upstream LLM, an OpenAI-compatible endpoint chosen per model by the policy.

    It speaks vendor JSON, so only the adapter and the proxy service may use it.
    Implementations raise UpstreamError, so the caller can answer `upstream_unavailable`.
    """

    async def chat(
        self,
        payload: dict[str, Any],
        *,
        base_url: str,
        timeout_s: float,
        api_key_env: str | None = None,
    ) -> dict[str, Any]:
        """POST the payload to `<base_url>/chat/completions`.

        The caller passes the model's `upstream_base_url`, `timeout_s` and `api_key_env` from the
        policy snapshot its request took, so the upstream never looks the policy up a second time.
        With `api_key_env`, the key in that env var is sent as a Bearer token; an empty one is an
        UpstreamError.
        """
        ...
