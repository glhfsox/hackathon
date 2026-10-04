# Architecture

The single description of the stack, components, data flow and design of the AI Control Layer. Shapes of data and endpoints live in [`contracts/`](../contracts/README.md), not here. Principles live in [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md).

Status: **draft v0.1**. The team confirms or changes it through small PRs. Undecided points are marked **OPEN**.

## 1. Overview

```
             ┌──────────────────────── AI Control Layer (backend) ────────────────────────┐
 agent ──────► /v1/chat/completions ─► OpenAI adapter ─► canonical request ─► pipeline ───┼──► upstream LLM
 (base_url)  │                                                              (checks)     │    (per model, from policy)
             │ /api/* ◄── dashboard: policy, audit, metrics                              │
             │        policy.yaml (hot reload) · audit.db (append-only) · Jev client     │──► Jev (remote) ─► fallback: OpenAI   
             └───────────────────────────────────────────────────────────────────────────┘
 frontend (React) ──► /api/*
```

## 2. Stack

| Part              | Choice                                                                                                           |
| ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| Backend           | Python 3.12, FastAPI, Pydantic v2, httpx (upstream + Jev calls), PyYAML, PyJWT (tokens)                                         |
| Upstream LLMs     | Any OpenAI-compatible endpoint, set per model in the policy. Default local Ollama at `http://localhost:11434/v1` |
| AI decision maker | Jev (remote LLM), with an OpenAI-compatible fallback model (OpenAI in the shipped policy, Ollama for tests)  |
| Frontend          | React + Vite + TypeScript, plain fetch through one API client module                                             |
| Tests             | pytest with data-driven YAML cases                                                                               |
| Ports             | backend `8000`, frontend `5173`, Ollama `11434`                                                                  |

## 3. Request lifecycle

The agent re-sends the full conversation on every step, so the layer is **stateless per request**.

1. **Auth.** `Authorization: Bearer <JWT>` is verified with the shared secret (HS256 only, `exp` required, see section 11). A missing, invalid or expired token gets 401 and is audited. The token's `sub` is the user and its `roles` decide which tools the user may use.
2. **Policy snapshot.** The request takes a reference to the active policy once and uses it to the end, so a hot reload never mixes versions.
3. **Adapt.** OpenAI JSON becomes a `CanonicalRequest` ([contracts/models.md](../contracts/models.md)).
4. **Checkpoint detection** on the request:
   - The last message is `role: tool` → checkpoint `tool_result`.
   - Otherwise → checkpoint `input`.
5. **Inbound tool filter.** Every tool in the request's `tools` that the user's roles do not allow is removed, so the model never sees it. Each removal is audited (section 11).
6. **Pipeline** runs the checks enabled for that checkpoint, as described in section 4. A block returns a refusal at once. Redactions modify the copy that is forwarded.
7. **Forward** the request to the upstream for `model`. If the upstream fails, the response is a refusal with reason `upstream_unavailable`.
8. **Outbound tool filter.** Every tool call in the reply that the user's roles do not allow is removed and replaced by a text notice, so the agent has nothing to run. Each decision is audited (section 11). This runs before the reply is checked, because a model can ask for a tool it was never shown.
9. **Checkpoint detection** on the reply:
   - The reply contains `tool_calls` → checkpoint `tool_call`.
   - Otherwise → checkpoint `output`. The pipeline runs again on the reply.
10. **Respond.** The response is OpenAI-format with an extra `control` field (the decision trace). Normal clients ignore it, and the playground displays it.
11. **Audit.** One `AuditRecord` is written per check result, plus one `turn_summary` row per checkpoint, grouped by `request_id`. Token usage and cost come from upstream `usage` and the model price in the policy.

`stream: true` is accepted and answered non-streamed.

## 4. Check pipeline

- Each check implements `run(request, settings) -> CheckResult` ([contracts/models.md](../contracts/models.md)).
- A check is **on when its policy section exists** (`checks.<id>`) and then runs at every checkpoint in the table below. There are no modes or profiles.
- The pipeline turns a check's raw verdict into the final action:

| Check verdict           | Action |
| ----------------------- | ------ |
| allow                   | allow  |
| redact (has redactions) | redact |
| block                   | block  |
| error / timeout         | block  |

- Order is cheapest first and fixed by `cost_rank` in code. The first final `block` stops the pipeline.

| #   | Check id         | Checkpoints                     | What it does                                                                          |
| --- | ---------------- | ------------------------------- | ------------------------------------------------------------------------------------- |
| 1   | `permissions`    | input, tool_call, tool_result   | The model is in `checks.permissions.allowed_models` (input, tool_result); the user's roles allow the tools the model asks for (tool_call). Last line of defence behind the tool filters (section 11) |
| 2   | `budget`         | input, tool_result              | Requests per minute, tokens per day, cost per day per user, with the most generous limits of the user's roles (`checks.budget.roles`); no budget for any of the roles blocks |
| 3   | `loop_detection` | input, tool_result              | Too many tool calls in one agent turn, or the same call with the same args repeated |
| 4   | `signatures`     | input, tool_call, tool_result   | Regex patterns from the external attack-signature feed                                |
| 5   | `tool_args`      | tool_call                       | Shell danger, destructive SQL, path traversal, unsafe deserialization                 |
| 6   | `pii_secrets`    | all four                        | Detects and redacts email, phone, PESEL, SSN, IBAN, card numbers, API keys            |
| 7   | `jev`            | all four                        | AI risk score compared against `jev_threshold`. Always last.                          |

`pii_secrets` runs before `jev`, so Jev only sees redacted text.

At `tool_call` the content checks (`signatures`, `pii_secrets`, `jev`) read one **tool-call view**: the task (last user message), the agent's reasoning (the reply text next to the calls) and the calls as JSON (`CanonicalRequest.tool_call_view`). `pii_secrets` redacts that view, so Jev judges the call next to its task without seeing PII. The arguments the agent runs are never changed. `tool_args` and `permissions` keep reading the structured calls, and run first: a rule block stops the call before Jev is asked.

## 5. Jev and fallback

- A single module, `core/jev.py`, is the only code that talks to an AI decision maker.
- Order of attempts:
  1. Jev, using the policy `jev.timeout_s`.
  2. On a timeout, error or unparseable answer, the policy `jev.fallback` model (any OpenAI-compatible endpoint, OpenAI in the shipped policy with its key from `api_key_env`; JSON-mode prompt).
  3. If both fail, the result is `error`, which blocks (fail closed).
- Input and output shapes: `JudgeInput` and `JudgeVerdict` in [contracts/models.md](../contracts/models.md). `decided_by` records which model answered.
- **OPEN:** Jev endpoint, auth, wire format and cost. Until these are known, the Jev adapter is a stub behind the same interface, and the fallback carries the demo.

## 6. Policy

- **File:** `backend/policy.yaml`. **OPEN:** schema, to be defined in `contracts/policy.example.yaml`.
- **Hot reload:** the backend checks the file's mtime every second. A changed file is parsed and validated (Pydantic), then swapped in atomically. An invalid file is logged, audited as `policy_rejected`, and the old policy stays active.
- **Edits through the API:** `PUT /api/policy` validates first, then writes the file, so the file stays the single source.
- **Access control:** the `permissions` and `roles` sections say which roles may use which tools (section 11). The policy has no list of users or callers: identity comes from the token.
- **Secrets:** the policy names the Jev API key by environment variable (`jev.api_key_env`) and never contains it. The token secret is read from `JWT_SECRET`, not from the policy.

## 7. Tool guard (removed)

The tool-execution guard (`POST /v1/tools/check`) was removed (AGENTS.md §8). Tool calls are judged only at the proxy's `tool_call` checkpoint, so an agent that ignores a blocked reply and runs the tool anyway is not stopped.

## 8. Audit and metrics

- **Table:** one table `audit_records` with the columns in [contracts/models.md](../contracts/models.md). Insert only, with no update or delete code paths.
- **Export:** `GET /api/audit/export?format=json|csv`, with the same filters as the list endpoint.
- **Metrics:** `GET /api/metrics` aggregates from the audit table: blocks by check, token and cost use per user today, and p50/p95 latency per check. Nothing is stored separately.

## 9. Frontend

| Page       | Content                                                                                           | Data source                              |
| ---------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Dashboard  | Posture (Jev threshold, checks on), blocks over time and by check, usage per user, latency per check | `/api/metrics`                           |
| Audit log  | Filterable table with export buttons                                                              | `/api/audit`, `/api/audit/export`        |
| Policy     | YAML editor with validate and save, showing field errors from the backend                         | `/api/policy`, `/api/policy/validate`    |
| Playground | Chat with a signed token (**OPEN:** how the playground gets one), showing which checks fired per message                            | `/v1/chat/completions` (`control` field) |

All calls go through `frontend/src/api/client.ts`. The backend URL comes from `VITE_API_URL`.

## 10. Tests

- Each `tests/cases/<check>.yaml` file holds a list of `{name, checkpoint, request, expect: {action, check}}`.
- One parametrized pytest runs every case through the pipeline with Jev mocked.
- Every check needs at least one `allow` case and one `block` or `redact` case.
- End-to-end smoke test: the demo agent runs against the backend with a mocked upstream.
- Run with `cd backend && pytest`.

## 11. Authorization: tokens and roles

Who the user is comes from a signed token. What the user may do with tools comes from the policy.

**Token.** `Authorization: Bearer <JWT>`, signed by the agent with a secret it shares with the layer.
- Algorithm HS256 only. Any other algorithm, including `none`, is rejected.
- The secret is the `JWT_SECRET` environment variable. The layer refuses to start without it.
- Claims: `sub` (the user, a non-empty string), `roles` (a list of role names) and `exp` (required, must be in the future).
- Any failure gives 401 and an `auth_failed` audit row whose reason names the problem (for example `ExpiredSignatureError`). The token itself is never logged.

**Policy.** Two sections map roles to tools through a permission layer:

```yaml
permissions:                    # permission -> the tools it grants
  customers.read: [query_customers]
  shell.exec:     [run_shell]
roles:                          # role -> the permissions it holds
  support:   [customers.read]
  developer: [customers.read, shell.exec]
```

- A user's allowed tools are the union over the permissions of all the roles in the token.
- **Deny by default.** A tool listed in no permission is allowed to nobody. A role the policy does not define grants nothing. Empty sections mean no tool is allowed, and the `policy_loaded` audit row says so.
- A role that names an undefined permission is a validation error, and so is an empty role or permission name.

**Enforcement.** Three points, all driven by the same allowed-tools set:
1. **Inbound** (lifecycle step 5). Tools the user may not use are removed from the request's `tools`. If none remain, `tools` and `tool_choice` are dropped. A `tool_choice` that forces a removed tool is dropped.
2. **Outbound** (step 8). A call to a tool the user may not use is removed from the reply and a text notice is appended to its content: `Tool call denied by policy: '<tool>'.` Calls to allowed tools stay. If no call remains, `finish_reason` becomes `stop`. The notice shows the tool name only if it is a plain identifier.
3. **Last line of defence.** The `permissions` check at `tool_call`.

**Audit.** One row per tool decision, with `check` set to `rbac`, `caller_id` set to the user and the tool name in `tools`. Removals from a request and replaced calls are `block` rows, and a reply's allowed calls are `allow` rows. The reason names the roles.

**Budgets and models.** Budgets are set per role (`checks.budget.roles`) and counted per user in an in-memory ledger, so they reset on restart and are not shared between processes. Allowed models are global (`checks.permissions.allowed_models`); a model with no upstream in `models` answers `upstream_unavailable`.
