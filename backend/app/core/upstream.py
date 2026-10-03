import os
from typing import Any

import httpx

from app.protocols.upstream import UpstreamError


class HttpUpstream:
    """The Upstream protocol over httpx, for any OpenAI-compatible endpoint (Ollama included).

    The http client is shared with the Jev client and owned by the application.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def chat(
        self,
        payload: dict[str, Any],
        *,
        base_url: str,
        timeout_s: float,
        api_key_env: str | None = None,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {}
        if api_key_env is not None:
            # The message names the variable only; the key itself is never logged.
            if not (key := os.environ.get(api_key_env)):
                raise UpstreamError(f"env {api_key_env} is empty")
            headers["Authorization"] = f"Bearer {key}"
        try:
            response = await self._http.post(url, json=payload, headers=headers, timeout=timeout_s)
            response.raise_for_status()
            body = response.json()
        # ValueError: the body is not JSON. Neither message quotes the body, which may carry PII;
        # the type name keeps a timeout, whose str() is "", readable.
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(body, dict):
            raise UpstreamError(f"the response is a JSON {type(body).__name__}, not an object")
        return body
