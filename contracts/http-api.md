# HTTP API

Base URL `http://localhost:8000`. All bodies are JSON unless noted. Model names refer to [models.md](models.md).

## Agent-facing (`/v1`, auth: `Authorization: Bearer <api key>`)

### `POST /v1/chat/completions`

Takes the standard OpenAI chat-completions request (`model`, `messages`, `tools`, `tool_choice`, `temperature`, `max_tokens`, `stream`).

- **Allowed:** the standard OpenAI response, plus a `control` field:
  ```json
  "control": { "request_id": "…", "decisions": [Decision, …] }
  ```
- **Blocked:** **HTTP 200**, in the standard shape, so agents keep running:
  ```json
  {
    "id": "ctl-<request_id>", "object": "chat.completion", "model": "<model>",
    "choices": [{ "index": 0, "finish_reason": "stop",
                  "message": { "role": "assistant",
                               "content": "Blocked by <check>: <reason>" } }],
    "usage": { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0 },
    "control": { "request_id": "…", "decisions": [ … ] }
  }
  ```
- **Errors:** `401` unknown key, `400` malformed body. The error body is `{ "error": { "message", "type" } }`, as in OpenAI. Both are audited.

### `POST /v1/tools/check`

The tool guard. Call it before executing a tool.

```json
// request
{ "tool_call": ToolCall, "messages": [Message, …] }
// response 200
{ "allowed": true | false, "decision": Decision }
```

## Dashboard-facing (`/api`, no auth: local demo only)

| Method & path | Request | Response |
|---------------|---------|----------|
| `GET /api/policy` | — | `{ "yaml": str, "version": str, "loaded_at": str }` |
| `POST /api/policy/validate` | `{ "yaml": str }` | `{ "valid": bool, "errors": [{ "loc": str, "msg": str }] }` |
| `PUT /api/policy` | `{ "yaml": str }` | 200 `{ "version", "loaded_at" }` or 422 `{ "errors": [...] }` (old policy stays) |
| `GET /api/audit` | query: `caller_id, check, action, checkpoint, since, until, limit=100, offset=0` | `{ "total": int, "items": [AuditRecord] }` |
| `GET /api/audit/export` | same filters + `format=json\|csv` | file download (`application/json` or `text/csv`) |
| `GET /api/metrics` | query: `since` | see below |
| `GET /api/health` | — | `{ "status": "ok", "policy_version", "jev": "up\|down", "fallback": "up\|down" }` |

`GET /api/metrics` response:
```json
{
  "active_profile": "balanced",
  "totals": { "requests": 0, "allowed": 0, "redacted": 0, "blocked": 0, "flagged": 0 },
  "blocks_by_check": { "<check>": 0 },
  "latency_ms_by_check": { "<check>": { "p50": 0.0, "p95": 0.0 } },
  "overhead_ms": { "p50": 0.0, "p95": 0.0 },
  "budget_by_caller": { "<caller_id>": { "tokens_today": 0, "tokens_limit": 0, "cost_today": 0.0, "cost_limit": 0.0 } }
}
```

The playground uses `POST /v1/chat/completions` with the `playground` caller key and renders the `control` field.
