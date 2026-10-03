# HTTP API

Base URL `http://localhost:8000`. All bodies are JSON unless noted. Model names refer to [models.md](models.md).

## Agent-facing (`/v1`, auth: `Authorization: Bearer <JWT>`)

The token is signed with HS256 using the secret the agent shares with the layer. Claims: `sub` (user), `roles` (list of role names), `exp` (required). See [architecture §11](../docs/architecture.md).

### `POST /v1/chat/completions`

Takes the standard OpenAI chat-completions request (`model`, `messages`, `tools`, `tool_choice`, `temperature`, `max_tokens`, `stream`).

Tool access follows the token's roles. The `tools` the model sees are only those the roles allow. A `tool_calls` entry in the reply that the roles do not allow is removed, and `Tool call denied by policy: '<tool>'.` is appended to the message `content`. When no call remains, `finish_reason` is `stop`. Both are in the standard response shape, with no extra field.

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
- **Errors:** `401` missing, invalid or expired token, `400` malformed body. The error body is `{ "error": { "message", "type" } }`, as in OpenAI. Both are audited.

### `POST /v1/tools/check`

The tool guard. Call it before executing a tool, with the same token. The user's roles decide which tools are allowed.

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
  "budget_by_caller": { "<user>": { "tokens_today": 0, "tokens_limit": 0, "cost_today": 0.0, "cost_limit": 0.0 } }
}
```

There are no budgets, so `budget_by_caller` holds today's usage per user (the token's `sub`) and both limits are always `0`, meaning unlimited. The name is kept so the shape stays compatible.

The playground uses `POST /v1/chat/completions` with a signed token and renders the `control` field. **OPEN:** how the playground obtains the token.
