# Configuration guide

How to set up and configure the AI Control Layer, and what every configuration word means. The design behind it is in [`architecture.md`](../5-implementation/docs/architecture.md). Paths below are relative to [`5-implementation/`](../5-implementation/). This guide is written from `backend/policy.yaml`, `backend/app/models/policy.py`, the checks in `backend/app/checks/`, `compose.yaml` and `.env.example`.

The layer has three places to configure:

| Where | What it controls | When a change takes effect |
|---|---|---|
| `.env` (environment variables) | Secrets and paths | Restart the backend |
| `backend/policy.yaml` (the policy) | All behaviour: models, roles, checks, Jev | About 2 s, no restart (hot reload) |
| `backend/signatures.yaml` (the signature feed) | Attack patterns for the `signatures` check | Every `signatures.refresh_s` seconds |

## Keyless judge setup

Run from `5-implementation/`:

```sh
docker compose -f compose.yaml -f compose.judges.yaml up -d --build
docker compose -f compose.yaml -f compose.judges.yaml run --rm test-app --scenario all
```

No provider keys are needed. The public relay shares a 1,500-call allowance and expires on 5 October 2026 at 23:59 Europe/Warsaw. See the [relay guide](../5-implementation/docs/judge-relay.md) for limits and deployment. The direct-provider instructions below apply when using your own keys and `compose.yaml` alone.

## 1. Quick start

### With Docker (recommended)

```bash
cd 5-implementation                   # compose.yaml lives here
docker compose run --rm init          # optional: writes .env with a random JWT_SECRET
# edit .env: set OPENAI_API_KEY (and TYPESAFE_API_KEY if you have one)
docker compose up -d --build          # backend on :8000, dashboard on :5173
docker compose run --rm test-app --scenario all
```

- Without `init`, `JWT_SECRET` falls back to a public demo value. Use it for a local demo only.
- The playground token is baked into the frontend image at build time. Rebuild the frontend after you change `JWT_SECRET`.
- `policy.yaml`, `signatures.yaml` and `logs/` are mounted from your checkout. Edits to them reach the running container.
- Ollama is optional and runs on the host. A relay makes `localhost:11434` in the policy work inside Docker.

### Without Docker

```bash
cd backend
uv sync
cp ../.env.example .env               # then fill in the values
uv run uvicorn app.main:app --reload --port 8000
```

Frontend: `cd frontend && npm install && npm run dev`. Set `VITE_API_URL` if the backend is not on `http://localhost:8000`.

## 2. Environment variables

Never put these values in the policy or in git.

| Variable | Required | Used by | Meaning |
|---|---|---|---|
| `JWT_SECRET` | yes | backend, demo agents, test_app, frontend build | Shared HS256 secret. The layer verifies tokens with it. Agents sign their tokens with it. The backend does not start without it. |
| `OPENAI_API_KEY` | for the demo | upstream `gpt-4o-mini`, Jev fallback | Read through `api_key_env` in the policy. Empty: those requests fail closed (`upstream_unavailable`, or a Jev block when Jev is down too). |
| `TYPESAFE_API_KEY` | no | Jev | Key of the remote Jev service. Empty: the Jev fallback model is asked instead. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (or `LANGFUSE_BASE_URL`) | no | tracing | Optional Langfuse tracing. Empty: off. |
| `POLICY_PATH` | no | backend | Policy file. Default `backend/policy.yaml`. |
| `LOGS_DIR` | no | backend | Audit log folder (`*.jsonl`). Default `backend/logs`. |
| `PLAYGROUND_MODEL` | no | compose, frontend | Model the playground requests. Default `gpt-4o-mini`. |
| `TEST_APP_MODEL` | no | test_app | Model test_app requests. Default `gpt-4o-mini`. |
| `TEST_APP_PROXY_URL` | no | test_app | Set: test_app goes through the layer. Unset: test_app calls the model directly. |
| `VITE_API_URL` | no | frontend (build time) | Backend URL. Default `http://localhost:8000`. |

## 3. The policy file

`backend/policy.yaml` is the only source of behaviour. Rules that apply to the whole file:

- **Hot reload.** The backend checks the file about once a second. A valid change is active within about 2 s.
- **Validated before use.** An invalid file is rejected with field-level errors and audited as `policy_rejected`. The previous policy stays active.
- **Strict keys.** An unknown key, a misspelled check id, a parameter a check does not take, a value of the wrong type, or a key written twice in one section is an error.
- **Snapshot per request.** A request uses one policy version from start to end. A reload never mixes two versions in one request.
- **Dashboard editor.** `PUT /api/policy` validates first and then writes this same file.
- **No secrets.** Refer to keys by the *name* of an environment variable (`api_key_env`).

Top-level structure:

```yaml
version: "0.2"         # label for audit records
jev_threshold: 0.6     # Jev blocks at or above this score
models: {...}          # upstream LLMs agents may call
permissions: {...}     # permission -> tools
roles: {...}           # role -> permissions
checks: {...}          # which checks are on, and their parameters
signatures: {...}      # where the attack-signature feed lives
jev: {...}             # the Jev service and its fallback
```

### 3.1 `version`

Required string. Written into every audit record together with a hash of the file content. Change it when you change the policy, so audit rows show which version decided.

### 3.2 `jev_threshold`

Required number from `0.0` to `1.0`. Jev returns a risk score (probability the content is risky). The request is blocked when the score is **at or above** the threshold. Lower value means stricter.

### 3.3 `models`

The upstream LLMs. The key is the `model` name an agent sends in its request.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `upstream_base_url` | yes | | Any OpenAI-compatible endpoint. The request is forwarded there. |
| `api_key_env` | no | none | Name of the env var with the upstream API key. Leave out for a local model. Named but empty: refusal `upstream_unavailable`. |
| `price_per_1k_tokens` | no | `0.0` | Price used for cost reporting and the `cost_per_day` budget. |
| `timeout_s` | no | `120` | Seconds to wait for the upstream. After that: refusal `upstream_unavailable`. |

```yaml
models:
  gpt-4o-mini:
    upstream_base_url: "https://api.openai.com/v1"
    api_key_env: OPENAI_API_KEY
    price_per_1k_tokens: 0.0006
    timeout_s: 60
```

A model must also be in `checks.permissions.allowed_models`, or requests for it are blocked.

### 3.4 `permissions` and `roles` (tool access, RBAC)

Two maps decide which tools a user may use. Deny by default.

- `permissions`: permission name → list of tool names it grants.
- `roles`: role name → list of permission names it holds.

```yaml
permissions:
  customers.read: [query_customers]
  shell.exec:     [run_shell]
roles:
  support:   [customers.read]
  developer: [customers.read, shell.exec]
```

Rules:

- A user's roles come from the `roles` claim of their JWT. Users are never listed in the policy.
- A user's allowed tools are the union of the tools of all permissions of all their roles.
- A tool in no permission is allowed to nobody.
- A role in the token that the policy does not define grants nothing.
- A role that names an undefined permission is a validation error.

Enforcement happens at three points:

1. **Inbound.** Tools the user may not use are removed from the request before the model sees them.
2. **Outbound.** A model call to a forbidden tool is removed from the reply. The text `Tool call denied by policy: '<tool>'.` replaces it.
3. **`permissions` check** at `tool_call`, as the last line of defence.

Each tool decision is an audit row with `check` = `rbac`.

### 3.5 `checks`

One section per check. The section key is the check id. Every key inside it is a parameter of that check.

- **On/off.** A check is on when its section exists. It is off when the section is left out. An empty section (`tool_args: {}` or `tool_args:`) turns it on with default parameters.
- **No modes.** An enabled check runs at every checkpoint it applies to.
- **Outcome.** A finding blocks. `pii_secrets` is the exception: it redacts and lets the request continue. A check error or timeout blocks (fail closed).
- **Order.** Fixed in code, cheapest first. The first block stops the pipeline. `jev` always runs last.
- The `policy_loaded` audit row names every check that is off.

| # | Check id | Runs at | Parameters |
|---|---|---|---|
| 1 | `permissions` | input, tool_call, tool_result | `allowed_models` |
| 2 | `budget` | input, tool_result | `roles` |
| 3 | `loop_detection` | input, tool_result | `max_tool_calls`, `max_repeats` |
| 4 | `signatures` | input, tool_call, tool_result | `categories`, `skip_roles` |
| 5 | `tool_args` | tool_call | `allowed_root`, `categories`, `max_command_chars` |
| 6 | `pii_secrets` | all four | `types`, `extra_patterns`, `skip_roles` |
| 7 | `jev` | all four | `timeout_s`, `max_chars`, `max_judge_calls`, `max_concurrency`, `skip_roles` |

Only `jev` accepts `timeout_s`. Rule checks are fast synchronous code.

#### `permissions`

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `allowed_models` | list of model names | `[]` | Models agents may request. Every name must exist under `models`. A request for any other model is blocked. |

At `tool_call` it also blocks a tool the user's roles do not allow (see 3.4).

#### `budget`

Usage limits per role, counted per user (the token's `sub`).

| Parameter | Type | Meaning |
|---|---|---|
| `roles.<role>.requests_per_minute` | integer ≥ 0 | Max requests to the model per minute. |
| `roles.<role>.tokens_per_day` | integer ≥ 0 | Max tokens per day. |
| `roles.<role>.cost_per_day` | number ≥ 0 | Max estimated cost per day, from `price_per_1k_tokens`. |

- Leave a limit out for no limit on it.
- A user with several roles gets the most generous limits among those roles.
- A user none of whose roles is listed here is blocked.
- Every role listed must exist under `roles`.
- Counters live in memory. They reset when the backend restarts.

#### `loop_detection`

Counts tool calls in the current agent turn (since the last user message).

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `max_tool_calls` | integer ≥ 0 | no limit | Block when the turn has more tool calls than this. |
| `max_repeats` | integer ≥ 0 | no limit | Block when one tool with identical arguments runs more times than this. |

With neither parameter set, the check allows everything.

#### `signatures`

Matches text against the external attack-signature feed (see 3.6).

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `categories` | list | all | Feed categories to apply: `prompt_injection`, `code_execution`, `deserialization`, `supply_chain`, `exfiltration`, `other`. An unknown name is an error. |
| `skip_roles` | list of `system`, `user`, `assistant`, `tool` | `[]` | Message roles not scanned. Use `[system]` because a defensive system prompt often quotes attacks. The model's new reply is always scanned. |

The check scans the whole forwarded conversation. A finding in history keeps blocking while the agent re-sends it. If the feed never loaded, the check fails closed.

#### `tool_args`

Inspects every string in the arguments of the tool calls the model asks for.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `allowed_root` | path | none | File paths in arguments must stay inside this directory. |
| `categories` | list | all four | Rule families: `shell` (destructive commands, remote exec, chaining), `sql` (destructive SQL), `path_traversal` (`..`, paths outside `allowed_root`), `deserialization` (pickle, unsafe YAML and similar). |
| `max_command_chars` | integer > 0 | `2000` | Max length of one shell command. A longer command is blocked as too long to inspect. |

The block reason names the tool, the argument path and the rule. It never quotes the value.

#### `pii_secrets`

Finds personal data and secrets and replaces them with `[REDACTED:<KIND>]`.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `types` | list | all | Detectors to run: `email`, `phone`, `ssn`, `pesel`, `iban`, `card`, `api_key`. |
| `extra_patterns` | list of `{kind, pattern}` | `[]` | Your own regexes. `kind` becomes the label in `[REDACTED:<KIND>]`. An invalid regex is an error. |
| `skip_roles` | list of message roles | `[]` | Roles not scanned. Usually `[system]`, because the agent owner writes it. |

At `tool_call` it redacts the tool-call view that Jev reads. The arguments the agent runs are never changed.

#### `jev`

The AI risk judge. Compares its score with `jev_threshold`.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `timeout_s` | seconds > 0 | `10` | Total time for the check. Set it above `jev.timeout_s` + `jev.fallback.timeout_s`, or a slow fallback is cut off and the request blocks. |
| `max_chars` | integer > 0 | `4000` | Size of one judged chunk. Longer text is judged chunk by chunk. |
| `max_judge_calls` | 1 to 1024 | `128` | Max chunks per request. Above it the check errors and blocks. |
| `max_concurrency` | 1 to 64 | `8` | Max judge calls in parallel. |
| `skip_roles` | list of message roles | `[]` | Roles not sent to Jev. |

Validation rules that protect PII:

- `jev` may be on only when `pii_secrets` is on. Jev never sees unredacted text.
- `jev.skip_roles` must include every role in `pii_secrets.skip_roles`.

### 3.6 `signatures` (feed location)

Top-level section. It says where the feed is. It does not turn the check on; `checks.signatures` does that.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `source` | yes | | File path (relative to the policy file) or http(s) URL of the feed. |
| `refresh_s` | no | `10` | Seconds between re-reads of the feed. |
| `timeout_s` | no | `5` | Seconds to wait when fetching a URL feed. |

If the feed is unreachable at load time, the policy is still accepted and the last good feed stays active.

Feed file format (`backend/signatures.yaml`):

```yaml
version: "2026-10-03.2"        # quote it
signatures:
  - id: PI-001                 # unique, cited in block reasons
    category: prompt_injection # see the category list in `checks.signatures`
    pattern: '\bignore\s+previous\s+instructions\b'   # Python regex, IGNORECASE by default
    description: Instruction override                 # shown in the block reason
    references: ["https://genai.owasp.org/llmrisk/llm01-prompt-injection/"]  # optional
    case_sensitive: false      # optional
```

- A `description` must match no pattern. Agents re-send refusals, and the whole conversation is scanned.
- A broken edit is rejected and the last good set stays active.

### 3.7 `jev` (service and fallback)

Top-level section. It says how to reach Jev. The `checks.jev` section turns the check on.

| Field | Default | Meaning |
|---|---|---|
| `model` | `jev-1.13.0` | Jev model. Must be a pinned version. `jev-latest` is rejected. |
| `api_key_env` | `TYPESAFE_API_KEY` | Name of the env var with the Jev key. |
| `base_url` | `https://api.typesafe.ai` | Jev endpoint. |
| `timeout_s` | `3` | After this, the fallback is asked. |
| `fallback.model` | required | Any OpenAI-compatible model. |
| `fallback.base_url` | required | Its endpoint. |
| `fallback.api_key_env` | none | Env var with its key. Leave out for Ollama. |
| `fallback.timeout_s` | `30` | Seconds to wait for the fallback. |

Order of attempts: Jev, then the fallback, then block. If both fail, the request is blocked (fail closed).

## 4. Common tasks

| Task | Edit |
|---|---|
| Turn a check off | Delete its section under `checks`. Remember: `jev` needs `pii_secrets`. |
| Make Jev stricter | Lower `jev_threshold`. |
| Give a role a new tool | Add the tool to a permission under `permissions`, and the permission to the role under `roles`. |
| Add a new role | Add it under `roles`. Also add it under `checks.budget.roles`, or its users are blocked. |
| Add a model | Add it under `models` and to `checks.permissions.allowed_models`. |
| Use Ollama only (offline) | Point `jev.fallback` at `http://localhost:11434/v1` with no `api_key_env`. Use `gemma4` as the agent model. |
| Add a custom PII pattern | Add `{kind: ORDER_ID, pattern: '...'}` to `checks.pii_secrets.extra_patterns`. |
| Add an attack pattern | Add an entry to `signatures.yaml`. No restart needed. |

## 5. Glossary

**Agent.** A client program that calls an LLM. It points its `base_url` at the layer and sends a JWT.

**Allow / redact / block.** The three actions. Allow forwards unchanged. Redact replaces sensitive text and forwards. Block stops the request and returns a refusal.

**`api_key_env`.** The *name* of an environment variable that holds a key. The policy never holds the key itself.

**Audit record.** One append-only row per check decision, plus one `turn_summary` row per checkpoint. Stored in `logs/*.jsonl`. Exportable from `/api/audit/export`.

**`auth_failed`.** Audit row for a missing, invalid or expired token. The HTTP answer is 401.

**Budget.** Per-role limits on requests, tokens and cost, counted per user.

**Canonical request.** The internal, vendor-neutral form of a request. Checks read only this form, never OpenAI JSON.

**Category.** A group of rules. In the signature feed it groups attack patterns. In `tool_args` it groups argument rules.

**Check.** One security test. Receives the canonical request and its parameters. Returns a verdict with a score, a reason and a latency.

**Check id.** The name of a check under `checks`: `permissions`, `budget`, `loop_detection`, `signatures`, `tool_args`, `pii_secrets`, `jev`.

**Checkpoint.** A point in the conversation where checks run:
- `input`: the prompt the agent sends.
- `tool_call`: the model's reply asks to run a tool.
- `tool_result`: data a tool returned, on its way back to the model.
- `output`: the model's final answer.

**`control` field.** Extra field in the response with the decision trace. Normal clients ignore it. The playground shows it.

**Cost rank.** The fixed order of checks, cheapest first.

**Fail closed.** On any error or timeout, block. Never allow on an error.

**Fallback.** The model asked when Jev is unavailable, slow or gives an unusable answer.

**Hot reload.** The backend picks up a valid policy change without a restart.

**Jev.** The remote AI decision maker (TypeSafe System One). Returns a risk score from 0 to 1.

**`jev_threshold`.** The score at or above which Jev blocks.

**JWT.** Signed token (HS256) sent as `Authorization: Bearer <token>`. Required claims: `sub`, `roles`, `exp`.

**Model (in `models`).** An upstream LLM agents may call, with its endpoint, price and timeout.

**Parameter.** A key inside a check's section. Each check accepts only its own parameters.

**Permission.** A named set of tools, for example `customers.read`.

**PII.** Personally identifiable information: email, phone, SSN, PESEL, IBAN, card number. Here also secrets such as API keys.

**Policy.** `backend/policy.yaml`. The only source of the layer's behaviour.

**`policy_loaded` / `policy_rejected`.** Audit rows for an accepted or a rejected policy.

**RBAC.** Role-based access control. Roles hold permissions. Permissions grant tools.

**Redaction.** Replacing found text with `[REDACTED:<KIND>]`.

**Refusal.** A normal OpenAI-format reply that explains a block, so agents do not crash.

**Role (RBAC).** A name in the JWT `roles` claim, defined under `roles` in the policy.

**Role (message).** The author of a message in the conversation: `system`, `user`, `assistant` or `tool`. Used by `skip_roles`.

**`skip_roles`.** Message roles a check does not scan.

**Signature.** A regex for a known attack, from the external feed.

**Signature feed.** The file or URL with attack signatures, re-read every `refresh_s` seconds.

**`sub`.** The JWT claim with the user's name. Budgets are counted per `sub`.

**Tool.** A function the model may ask the agent to run, for example `run_shell`.

**Tool-call view.** The text content checks read at `tool_call`: the task, the agent's reasoning and the calls as JSON.

**Upstream.** The real LLM endpoint the layer forwards an allowed request to.

**`upstream_unavailable`.** Refusal reason when the upstream fails, times out or has no API key.

**Verdict.** A check's raw answer: `allow`, `redact`, `block` or `error`. The pipeline turns it into the final action.

**`version`.** Policy label written into every audit record.
