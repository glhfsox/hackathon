# Backend: AI Control Layer

Short notes for whoever continues here. Terse on purpose. Rules: root `AGENTS.md`. Design: `docs/architecture.md`. Shapes: `contracts/`.

## What it is

OpenAI-compatible proxy. Agent changes `base_url`. Every request passes checks. Result: allow, redact or block. Every decision is logged.

## Run

```bash
cd backend
uv sync
cp .env.example .env          # fill keys, see below
uv run uvicorn app.main:app --reload --port 8000
```

Check it:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest -q
```

Env (`.env`, never committed):

| Var | Needed | What |
|---|---|---|
| `TYPESAFE_API_KEY` | no | Jev. Empty: local Ollama fallback |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | no | Tracing. Empty: off |
| `POLICY_PATH` | no | Default `backend/policy.yaml` |
| `LOGS_DIR` | no | Default `backend/logs` |

Upstream model: Ollama at `http://localhost:11434/v1`, model `gemma4`. Change in `policy.yaml` under `models`.

## Layout

```
app/
  main.py            create_app: builds everything in the lifespan
  deps.py            routes get services from here (protocols only)
  api/               HTTP routes: chat, tools, policy, audit, metrics, health
  schemas/           request/response bodies of the routes
  adapters/openai.py vendor JSON <-> canonical. Only place that knows OpenAI shape
  models/            canonical models, enums, AuditRecord, Usage, policy schema (policy.py)
  protocols/         interfaces: Check, CheckContext, Judge, AuditSink, PolicyProvider, Upstream, ProviderAdapter
  core/
    proxy.py         ProxyService: one request end to end
    pipeline.py      Pipeline + run_checkpoint: runs checks, verdict -> action, audit rows
    policy_store.py  PolicyStore: load, validate, hot reload, save
    jev.py           JevClient: Jev -> Ollama fallback -> fail closed, cache, breaker
    budget.py        UsageLedger: usage counters per caller_id (today: one, `anonymous`)
    signatures.py    SignatureFeed: external attack patterns, auto refresh
    metrics.py       compute_metrics from audit rows
    upstream.py      HTTP client to the model
  checks/            one file per check + base.py helpers
  observability/     JSONL sink, fan-out sink, Langfuse sink, stats exporter
policy.yaml          the only source of behaviour. Hot reloaded
signatures.yaml      example attack-signature feed
tests/               unit tests + tests/cases/*.yaml (data-driven cases)
demo/                two demo agents + tools. Not scored
```

## One request

1. No auth: caller_id is always `anonymous` (`ANONYMOUS_CALLER` in `core/proxy.py`). JWT comes later.
2. Take ONE policy snapshot. Use it to the end.
3. Adapter: OpenAI JSON -> `CanonicalRequest`.
4. Checkpoint: last message is `tool` -> `tool_result`, else `input`.
5. `Pipeline.run`. Block -> refusal (HTTP 200, `Blocked by <check>: <reason>`). Upstream not called.
6. `ledger.record_request`. Call upstream. `ledger.record_usage`.
7. Reply checkpoint: has tool calls -> `tool_call`, else `output`. Pass ORIGINAL messages here, not the redacted copy.
8. Response = upstream reply (maybe redacted) + `control` field with all decisions.

## Checks

Order = cost, cheapest first. First block stops.

| # | id | Runs at | Does |
|---|---|---|---|
| 1 | `permissions` | input, tool_call, tool_result | Model and tool in `allowed_models` / `allowed_tools` |
| 2 | `budget` | input, tool_result | Requests/min, tokens/day, cost/day |
| 3 | `loop_detection` | input, tool_result | Too many or repeated tool calls in one agent turn |
| 4 | `signatures` | input, tool_call, tool_result | Regex feed of known attacks |
| 5 | `tool_args` | tool_call | Shell, SQL, path traversal, deserialization |
| 6 | `pii_secrets` | all four | Redacts email, phone, SSN, PESEL, IBAN, card, keys |
| 7 | `jev` | all four | AI risk score vs `jev_threshold`. Always last. At tool_call it judges the tool-call view (task + reasoning + calls, PII redacted) |

No modes. A check is on when its `checks.<id>` section exists. Verdict -> action: allow -> allow, redact -> redact, block -> block, error / timeout -> block.

Error never means allow.

### Add a check

1. `app/checks/<id>.py`: class with `id`, `cost_rank`, `checkpoints`, `async run(request, settings, ctx)`. Module-level `CHECK = MyCheck()`.
2. Return raw verdict via `make_result(...)`. Never raise: bad state -> verdict `error`.
3. Add the name to `_MODULES` in `app/checks/__init__.py`.
4. Add id, checkpoints and params to `CHECK_SPECS` in `app/models/policy.py`. A test fails if it drifts from the registry.
5. Add `tests/cases/<id>.yaml` with at least one `allow` and one `block`/`redact` case. A test enforces this.

`settings` = the check's params from policy. `ctx` = live objects: ledger, signatures, judge, Jev threshold.

Reasons go to audit, logs and the refusal text. Never put matched values, argument text or model-written text in a reason.

## Policy

`policy.yaml`. Polled every second. Valid edit applies to the next request. Invalid edit: rejected, old policy stays, audit `policy_rejected`.

Rejected at load: unknown keys, unknown check id, an old mode key (`input: block`) or `profiles`/`active_profile`, unknown or mistyped param, duplicate YAML keys, `jev` on where `pii_secrets` is off, `jev.skip_roles` missing a role `pii_secrets` skips.

- `jev_threshold`: Jev blocks at or above this score.
- `checks.<id>`: present = on, every key is a param. Leave the section out to turn the check off.
- `checks.permissions`: `allowed_models`, `allowed_tools` (left out = nothing allowed). `checks.budget`: `requests_per_minute`, `tokens_per_day`, `cost_per_day` (left out = unlimited). Global, for every request.
- `models`: upstream URL, price, timeout.
- `skip_roles: [system]` on signatures, pii_secrets, jev: system prompt is not scanned. Remove to scan it.

API: `GET /api/policy`, `POST /api/policy/validate`, `PUT /api/policy` (writes the file, same validation).

## Jev

`core/jev.py` is the only code that talks to it. Wire format: TypeSafe System One, `POST {base_url}/v1/systemone`. Score = probability of risk. No key or failure -> Ollama fallback -> both fail: check errors -> fail closed. Verdicts cached by content, so re-sent history costs nothing. Breaker skips Jev for 30 s after a failure.

Local fallback is slow: 15-35 s per verdict with `gemma4`. Raise `jev.fallback.timeout_s` and `checks.jev.timeout_s` for demos without a Jev key, or remove the `checks.jev` section.

## Logs and metrics

No Langfuse needed. Everything goes to `logs/`:

- `audit-YYYY-MM-DD.jsonl`: one flat row per check result, plus one `turn_summary` row per checkpoint (session id, agent step, tools, tokens, cost, upstream latency, overhead, blocked_by). Tokens and cost live only on `turn_summary`, so `SUM(tokens)` is safe.
- `metrics-latest.json`, `metrics-history.jsonl`: snapshots.

```bash
uv run python -m app.observability.stats --logs logs/ --policy policy.yaml --csv audit.csv --turns-csv turns.csv
```

Power BI: load the JSONL folder or the CSV. Details and ready DAX measures: `docs/observability.md`.

API: `GET /api/audit`, `GET /api/audit/export?format=json|csv`, `GET /api/metrics`, `GET /api/health`.

Langfuse: set the three env vars. One trace per request, one observation per check. Sends decision metadata only, never message text.

## Changed in the merged proxy code

- `models/`: added `audit_record.py`, `usage.py`, `policy.py`; `PolicySnapshot` now carries the parsed `policy`; `ToolCall.arguments` defaults to `{}`; `Verdict` alias exported.
- `protocols/check.py`: `run` takes a third argument `ctx`.
- `protocols/policy_provider.py`: `save` is async.
- `core/pipeline.py`: `Pipeline.run(request, snapshot, *, usage=None)`.

## Known gaps

- Audit store is JSONL, not SQLite. Same `AuditSink` interface; swap the reader in `core/audit_reader.py`.
- No tool guard: an agent that ignores a blocked reply and runs the tool anyway is not stopped.
- PII in tool-call arguments is redacted only in the view the checks read, never in the arguments the agent runs.
- `rm -f *` (no recursion) passes `tool_args`.
- Rule checks take ~9 ms on 20 KB, ~46 ms on 200 KB ASCII, ~160 ms on 200 KB Cyrillic. Target was 50 ms.
- A finding in chat history keeps blocking while the agent re-sends it. The playground must drop the blocked message.
- Langfuse sink not verified against a live server.
- Removing the `signatures:` section from the policy at runtime keeps the last loaded feed.
