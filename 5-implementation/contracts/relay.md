# Judge provider relay

The relay transports provider requests only; it does not run or replace control-layer checks.

| Route | Behavior |
|---|---|
| `POST /openai/v1/chat/completions` | Non-streaming chat completion; approved model only; one completion; bounded output tokens. Adds private OpenAI bearer key. |
| `GET /openai/v1/models` | Local list of approved models, no provider request. |
| `POST /typesafe/v1/systemone` | System One model/state/questions body, approved pinned model only; exactly `risky` (binary `noul`) and `category` (`choice`, up to seven criteria). Adds private TypeSafe bearer key. |
| `GET /health` | Public configuration status, no credentials or provider call. |

No client authentication is required. Caller authorization, URLs, and query strings are never forwarded. Errors use `{ "error": { "message": string, "type": "relay_error" } }`; upstream error bodies and headers are not returned. Successful JSON responses preserve provider shapes. Unsupported methods/routes return 405/404; invalid bodies 400; oversized bodies 413; exhausted quota 429; expired access 410; storage/provider failures 503/502.

Deployment settings control allowed models, maximum body bytes, maximum output tokens, upstream timeout, total calls, minute calls, enabled flag and UTC expiry. Defaults are disabled; activation requires nonzero quotas and future expiry. Every attempted provider call reserves capacity transactionally before HTTP, including upstream failures. Static models/health routes do not reserve capacity. Cloud request/logging costs remain possible after provider access shuts down.

`jev.api_key_env` additionally accepts `null`: this explicitly sends no bearer token and attempts Jev without a local key. Omitting the field retains `TYPESAFE_API_KEY` and existing missing-key fallback behavior. Model/fallback `api_key_env: null` already supports this transport.

Configure `models.gpt-4o-mini.upstream_base_url` and `jev.fallback.base_url` as `<relay>/openai/v1`, `jev.base_url` as `<relay>/typesafe`. All three use `api_key_env: null`. The judge policy remains editable locally; relay infrastructure limits cannot be changed through that policy.
