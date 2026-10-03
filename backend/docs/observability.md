# Observability: JSONL audit log, metrics snapshots, Langfuse

Every decision of the control layer is already written to an `AuditSink` (one record per check
result, one `turn_summary` record per checkpoint, plus system events). Observability adds sinks
next to it; the checks are unchanged. Code: `app/sinks.py`, `app/stats.py`, `app/tracing.py`.

- **Power BI / files:** `JsonlAuditSink` writes every record as a flat JSON line, and
  `StatsExporter` keeps a metrics snapshot and a metrics time series next to it. Power BI shows
  them after a refresh.
- **Live view:** Langfuse shows each request as a trace as it happens. Optional, off unless keys
  are set.

## Files

All in the folder given to `JsonlAuditSink` (e.g. `backend/logs/`; keep it out of git).

| File | Written by | Content |
|------|------------|---------|
| `audit-YYYY-MM-DD.jsonl` | `JsonlAuditSink` | One row per audit record, append-only. The day is the UTC day of the record's `ts`. |
| `metrics-latest.json` | `StatsExporter` | The `GET /api/metrics` body plus `generated_at`, for the current UTC day. Replaced atomically every interval. |
| `metrics-history.jsonl` | `StatsExporter` | One flat line per interval, append-only. The numbers are totals for the UTC day as of `ts`. |

### Audit row columns (`audit-*.jsonl`, and the CLI's CSV)

| Column | Meaning |
|--------|---------|
| `id` | Increasing row id; continues across restarts. One writing process per folder. |
| `ts` | ISO 8601 timestamp as the layer wrote it (UTC). |
| `request_id`, `caller_id`, `model` | Null on system events that have none. |
| `checkpoint` | `input`, `tool_call`, `tool_result`, `output`; null on system events. |
| `check` | Check id, or a system event (`policy_loaded`, `policy_rejected`, `signature_feed_*`, `auth_failed`, `bad_request`, `upstream_unavailable`, `turn_summary`). |
| `action` | `allow`, `redact`, `block`, `flag`. |
| `reason` | Why. Never contains the matched secret or PII. |
| `score`, `latency_ms`, `decided_by` | `decided_by` is `rules`, `jev` or `fallback`. |
| `tokens`, `cost` | Upstream usage, on one row per request (its `turn_summary` row at `tool_call`/`output`), so summing the column does not double count. Cost is `tokens / 1000 * price_per_1k_tokens` of the model in the policy. |
| `policy_version` | Policy snapshot used, `version+content hash`. |
| `conversation_id` ... `blocked_by` | Agent and economic columns, filled on `turn_summary` rows only; see [Agent and economic metrics](#agent-and-economic-metrics-in-power-bi). |
| `date` | UTC date, `YYYY-MM-DD`. |
| `hour` | UTC hour, 0-23. |
| `minute` | UTC minute, `YYYY-MM-DDTHH:MM:00Z` (same format as the metrics `timeline`). |
| `is_block`, `is_redact`, `is_flag` | Booleans from `action`. |
| `is_system_event` | True for a system event (known id or no checkpoint). Exclude these rows from per-check charts. |
| `category` | Attack category from a `signatures` or `jev` reason (first one named), else null. Best effort. |

`metrics-history.jsonl` columns: `ts`, `active_profile`, `requests`, `allowed`, `redacted`,
`blocked`, `flagged`, `tokens_total`, `cost_total`, `overhead_p50_ms`, `overhead_p95_ms`,
`rules_overhead_p50_ms`, `rules_overhead_p95_ms`, `jev_latency_p50_ms`, `jev_latency_p95_ms`,
`sessions`, `turns`, `blocked_before_upstream`.

## Power BI

**Simplest:** export a CSV and load it with *Get Data -> Text/CSV*:

```bash
uv run python -m app.observability.stats --logs logs/ --policy policy.yaml --csv audit.csv   # metrics JSON on stdout
uv run python -m app.observability.stats --logs logs/ --policy policy.yaml --since 2026-10-03T12:00:00Z
uv run python -m app.observability.stats --logs logs/ --policy policy.yaml --turns-csv turns.csv  # turn_summary rows only
```

**Live folder:** *Get Data -> Folder*, pick the logs folder, then *Transform Data* and replace the
query in the Advanced Editor with the following. It reads every `audit-*.jsonl` line and skips a
line still being written. Use the same query with `"metrics-history"` for the time series.
*Refresh* reloads the data.

```powerquery
let
    Source = Folder.Files("C:\path\to\logs"),
    Files = Table.SelectRows(Source, each Text.StartsWith([Name], "audit-") and [Extension] = ".jsonl"),
    Lines = List.Combine(List.Transform(Files[Content], each Lines.FromBinary(_, null, null, 65001))),
    Rows = List.RemoveNulls(List.Transform(Lines, each try Json.Document(_) otherwise null)),
    Table = Table.FromRecords(Rows, null, MissingField.UseNull)
in
    Table
```

`metrics-latest.json` is a single JSON document: *Get Data -> JSON*.

## Agent and economic metrics in Power BI

The pipeline writes one `turn_summary` row per checkpoint, also when every check is off. These
rows are the agent and economic fact table: filter the audit rows to `check == "turn_summary"`
(add `Turns = Table.SelectRows(Table, each [check] = "turn_summary")` to the folder query above),
or load `turns.csv` from `--turns-csv`. They are system events (`is_system_event` true), so
per-check charts that already exclude system events are unchanged. The same numbers, computed by
the backend, are under `agents` and `economics` in `metrics-latest.json` and `GET /api/metrics`.

A **turn** is one request, i.e. one agent step: up to two rows with the same `request_id`, one at
`input`/`tool_result` (before the model) and, unless that one blocked, one at `tool_call`/`output`
(the model's reply). A **session** is one `conversation_id`.

| Column | Meaning on a `turn_summary` row |
|--------|---------------------------------|
| `action`, `blocked_by` | Decision for the checkpoint; the blocking check id, else null. |
| `reason` | `allowed`, `blocked by <check>`, `redacted by <checks>`, `flagged by <checks>`. |
| `score` | Highest check score at this checkpoint. |
| `latency_ms`, `overhead_ms` | Time the checks added at this checkpoint (sum of their latencies). |
| `decided_by` | `jev`/`fallback` when Jev's verdict blocked, else `rules`. |
| `conversation_id` | Short hash of the caller and the first user message. The agent re-sends the whole conversation every step, so all steps of a session share it with no client changes. Two sessions of one caller that start with the same first user message merge. |
| `step` | Assistant messages already in the conversation: 0 for the first step. |
| `messages` | Conversation length. |
| `tool_calls`, `tools` | Tool calls the model requested in this reply and their names, comma-joined, one per call. Null before the model. |
| `tool_calls_total` | Tool calls in the conversation so far, this reply included. |
| `tokens`, `prompt_tokens`, `completion_tokens`, `cost` | Upstream usage and its cost, on the reply row only. |
| `upstream_latency_ms` | Time the model took, on the reply row only. |

Ready measures (DAX, on a table `Turns` of these rows):

```dax
-- 1. Cost per caller per day: matrix with caller_id on rows and date on columns.
Cost = SUM(Turns[cost])
-- 2. Tokens by model: use with model on the axis.
Tokens = SUM(Turns[tokens])
Prompt tokens = SUM(Turns[prompt_tokens])
Completion tokens = SUM(Turns[completion_tokens])
-- 3. Sessions and average steps. The input/tool_result row always sees the conversation as the
--    agent sent it, so sessions are counted there.
Turn count = DISTINCTCOUNT(Turns[request_id])
Sessions = CALCULATE(DISTINCTCOUNT(Turns[conversation_id]), Turns[checkpoint] IN {"input", "tool_result"})
Avg steps per session = DIVIDE([Turn count], [Sessions])
Cost per session = DIVIDE([Cost], [Sessions])
-- 4. Tool usage: total here; per tool, split `tools` by "," into rows in Power Query
--    (Split Column -> By Delimiter -> Rows) and count rows by `tools`.
Tool calls = SUM(Turns[tool_calls])
-- 5. Block rate: share of turns that were blocked at either checkpoint.
Blocked turns = CALCULATE(DISTINCTCOUNT(Turns[request_id]), Turns[action] = "block")
Block rate = DIVIDE([Blocked turns], [Turn count])
-- 6. Overhead share: part of the latency the agent saw that the layer added. The backend's
--    agents.overhead_share counts only turns that reached the model; this one counts all.
Overhead share = DIVIDE(SUM(Turns[overhead_ms]), SUM(Turns[overhead_ms]) + SUM(Turns[upstream_latency_ms]))
-- 7. Budget utilisation: limits live in the policy, not in the audit log. Read
--    economics.budget_utilization from metrics-latest.json (percent of today's limit, null when
--    unlimited), or keep a small Limits table (caller_id, tokens_per_day) related on caller_id
--    and filter the visual to today's `date` (UTC):
Token budget % = DIVIDE([Tokens], MAX(Limits[tokens_per_day])) * 100
-- 8. Requests stopped before the paid model was called.
Stopped before model = CALCULATE(COUNTROWS(Turns), Turns[action] = "block", Turns[checkpoint] IN {"input", "tool_result"})
```

Rows written before `turn_summary` existed have no such rows, so they do not show up here; their
usage still counts in `tokens_total`/`cost_total`.

## Langfuse (live traces)

Set three environment variables before starting the backend. Without both keys the sink is not
created and nothing touches the network.

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or https://us.cloud.langfuse.com, or your own URL
```

**Cloud:** create a project at cloud.langfuse.com and copy its API keys. **Self-hosted:** run
Langfuse's official Docker Compose setup (`git clone https://github.com/langfuse/langfuse.git`,
then `docker compose up` in that folder; see langfuse.com/self-hosting). Open
`http://localhost:3000`, create a project and its API keys, and set
`LANGFUSE_HOST=http://localhost:3000`. We do not ship a compose file.

**What a trace looks like:** one trace per `request_id`, named `control-layer`, with
`user_id` = caller and trace metadata `model` and `policy_version`. Each check row is a
`guardrail` observation named `<checkpoint>/<check>` (e.g. `input/pii_secrets`,
`tool_call/tool_args`). Its output is `{action, reason}`, its metadata is the whole audit record,
and its version is the policy version. Level is `ERROR` for block and `WARNING` for flag. A Jev
verdict adds a numeric score `jev_risk`. A block adds a categorical score `blocked_by` (the check
id) and the tags `block` and `blocked_by:<check>`. Flag and redact rows add the tag `flag` or
`redact`. Filter the trace list by tag to see blocked requests. Policy reloads and signature-feed events
appear as standalone traces tagged `system_event`. Each `turn_summary` row is an event in its
request's trace, named `<checkpoint>/turn_summary`, with the agent and economic fields in its
metadata and no tags.

**Timing:** the SDK (4.16.0) cannot set an observation's start time. An observation starts when
the record reaches the sink (right after the check finished) and lasts the check's `latency_ms`.
The exact `ts` is in its metadata.

**Privacy:** only audit-record fields are sent. Message contents, tool arguments and tool results
are never sent: the record has no field for them, and trace input/output are never set.

## Wiring (app owner)

```python
import asyncio
from pathlib import Path

from app.observability.sinks import FanoutAuditSink, JsonlAuditSink
from app.observability.stats import StatsExporter
from app.observability.tracing import build_langfuse_sink

audit = FanoutAuditSink(
    [JsonlAuditSink(Path("logs")), *([lf] if (lf := build_langfuse_sink()) else [])]
)
exporter = StatsExporter(Path("logs"), lambda: store.current().policy, interval_s=5)

# lifespan startup, after store.load_initial():
exporter.start()
# lifespan shutdown:
await exporter.stop()
if lf:
    await asyncio.to_thread(lf.shutdown)  # sends what is still queued; blocks on the network
```

The first sink is the primary. If it fails, the error propagates and the request fails closed
(an unaudited decision is not allowed). A failing secondary such as Langfuse is only logged. If
the app also has a sink that the API reads from (memory or SQLite), make that the primary and
JSONL a secondary.
