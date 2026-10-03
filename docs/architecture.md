# Architecture

The single description of the stack, components, data flow and design of the AI Control Layer. Shapes of data and endpoints live in [`contracts/`](../contracts/README.md), not here. Principles live in [`.specify/memory/constitution.md`](../.specify/memory/constitution.md).

Status: **draft v0.1**. The team confirms or changes it through small PRs. Undecided points are marked **OPEN**.

## 1. Overview

```
             ┌──────────────────────── AI Control Layer (backend) ────────────────────────┐
 agent ──────► /v1/chat/completions ─► OpenAI adapter ─► canonical request ─► pipeline ───┼──► upstream LLM
 (base_url)  │                                                              (checks)     │    (per model, from policy)
             │ /v1/tools/check ◄── tool guard (agent asks before executing a tool)       │
             │ /api/* ◄── dashboard: policy, audit, metrics                              │
             │        policy.yaml (hot reload) · audit.db (append-only) · Jev client     │──► Jev (remote) ─► fallback: local Ollama
             └───────────────────────────────────────────────────────────────────────────┘
 frontend (React) ──► /api/*
```

## 2. Stack

| Part              | Choice                                                                                                           |
| ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| Backend           | Python 3.12, FastAPI, Pydantic v2, httpx (upstream + Jev calls), PyYAML                                          |
| Upstream LLMs     | Any OpenAI-compatible endpoint, set per model in the policy. Default local Ollama at `http://localhost:11434/v1` |
| AI decision maker | Jev (remote LLM), with a local Ollama model as fallback                                                          |
| Frontend          | React + Vite + TypeScript, plain fetch through one API client module                                             |
| Tests             | pytest with data-driven YAML cases                                                                               |
| Ports             | backend `8000`, frontend `5173`, Ollama `11434`                                                                  |

## 3. Request lifecycle

The agent re-sends the full conversation on every step, so the layer is **stateless per request** except for budgets and rate limits.

1. **Auth.** `Authorization: Bearer <key>` is matched against the policy callers. An unknown key gets 401 and is audited.
2. **Policy snapshot.** The request takes a reference to the active policy once and uses it to the end, so a hot reload never mixes versions.
3. **Adapt.** OpenAI JSON becomes a `CanonicalRequest` ([contracts/models.md](../contracts/models.md)).
4. **Checkpoint detection** on the request:
   - The last message is `role: tool` → checkpoint `tool_result`.
   - Otherwise → checkpoint `input`.
5. **Pipeline** runs the checks enabled for that checkpoint, as described in section 4. A block returns a refusal at once. Redactions modify the copy that is forwarded.
6. **Forward** the request to the upstream for `model`. If the upstream fails, the response is a refusal with reason `upstream_unavailable`.
7. **Checkpoint detection** on the reply:
   - The reply contains `tool_calls` → checkpoint `tool_call`.
   - Otherwise → checkpoint `output`. The pipeline runs again on the reply.
8. **Respond.** The response is OpenAI-format with an extra `control` field (the decision trace). Normal clients ignore it, and the playground displays it.
9. **Audit.** One `AuditRecord` is written per check result, grouped by `request_id`. Token usage and cost come from upstream `usage` and the model price in the policy.

`stream: true` is accepted and answered non-streamed.

## 4. Check pipeline

- Each check implements `run(request, settings) -> CheckResult` ([contracts/models.md](../contracts/models.md)).
- The policy gives each check a **mode per checkpoint**: `off | monitor | redact | block`.
- The pipeline turns a check's raw verdict into the final action:

| Check verdict           | `monitor` | `redact` | `block` |
| ----------------------- | --------- | -------- | ------- |
| allow                   | allow     | allow    | allow   |
| redact (has redactions) | flag      | redact   | redact  |
| block                   | flag      | block    | block   |
| error / timeout         | flag      | block    | block   |

- Order is cheapest first and fixed by `cost_rank` in code. The first final `block` stops the pipeline.

| #   | Check id         | Checkpoints                     | What it does                                                                          |
| --- | ---------------- | ------------------------------- | ------------------------------------------------------------------------------------- |
| 1   | `permissions`    | input, tool_call                | Caller may use this model and these tools                                             |
| 2   | `budget`         | input                           | Requests per minute, tokens per day, cost per day for the caller                      |
| 3   | `loop_detection` | input, tool_result              | Too many tool calls in the conversation, or the same call with the same args repeated |
| 4   | `signatures`     | input, tool_call, tool_result   | Regex patterns from the external attack-signature feed                                |
| 5   | `tool_args`      | tool_call                       | Shell danger, destructive SQL, path traversal, unsafe deserialization                 |
| 6   | `pii_secrets`    | tool_result, output (and input) | Detects and redacts email, phone, PESEL, SSN, IBAN, card numbers, API keys            |
| 7   | `jev`            | input, tool_result, output      | AI risk score compared against the threshold. Always last.                            |

`pii_secrets` runs before `jev` on any checkpoint where both are on, so Jev only sees redacted text.

## 5. Jev and fallback

- A single module, `core/jev.py`, is the only code that talks to an AI decision maker.
- Order of attempts:
  1. Jev, using the policy `jev.timeout_s`.
  2. On a timeout, error or unparseable answer, the policy `jev.fallback` model (local Ollama, OpenAI-compatible, JSON-mode prompt).
  3. If both fail, the result is `error`, which is treated as fail closed in modes `redact` and `block`.
- Input and output shapes: `JudgeInput` and `JudgeVerdict` in [contracts/models.md](../contracts/models.md). `decided_by` records which model answered.
- **OPEN:** Jev endpoint, auth, wire format and cost. Until these are known, the Jev adapter is a stub behind the same interface, and the fallback carries the demo.

## 6. Policy

- **File:** `backend/policy.yaml`. **OPEN:** schema, to be defined in `contracts/policy.example.yaml`.
- **Hot reload:** the backend checks the file's mtime every second. A changed file is parsed and validated (Pydantic), then swapped in atomically. An invalid file is logged, audited as `policy_rejected`, and the old policy stays active.
- **Edits through the API:** `PUT /api/policy` validates first, then writes the file, so the file stays the single source.
- **Secrets:** the policy names API keys by environment variable (`api_key_env`) and never contains them.

## 7. Tool guard

`POST /v1/tools/check` takes a tool call plus the conversation and runs the `tool_call` checkpoint. The demo agent wraps every tool with a decorator that calls this endpoint before executing and refuses on `block`. This stops an agent that ignores the verdict in a proxy reply.

## 8. Audit and metrics

- **Table:** one table `audit_records` with the columns in [contracts/models.md](../contracts/models.md). Insert only, with no update or delete code paths.
- **Export:** `GET /api/audit/export?format=json|csv`, with the same filters as the list endpoint.
- **Metrics:** `GET /api/metrics` aggregates from the audit table: blocks by check, budget used per caller, and p50/p95 latency per check. Nothing is stored separately.

## 9. Frontend

| Page       | Content                                                                                           | Data source                              |
| ---------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Dashboard  | Posture (profile, checks on), blocks over time and by check, budget per caller, latency per check | `/api/metrics`                           |
| Audit log  | Filterable table with export buttons                                                              | `/api/audit`, `/api/audit/export`        |
| Policy     | YAML editor with validate and save, showing field errors from the backend                         | `/api/policy`, `/api/policy/validate`    |
| Playground | Chat with a playground API key, showing which checks fired per message                            | `/v1/chat/completions` (`control` field) |

All calls go through `frontend/src/api/client.ts`. The backend URL comes from `VITE_API_URL`.

## 10. Tests

- Each `tests/cases/<check>.yaml` file holds a list of `{name, checkpoint, request, expect: {action, check}}`.
- One parametrized pytest runs every case through the pipeline with Jev mocked.
- Every check needs at least one `allow` case and one `block` or `redact` case.
- End-to-end smoke test: the demo agent runs against the backend with a mocked upstream.
- Run with `cd backend && pytest`.
