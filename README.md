# AI Control Layer

## Overview

AI Control Layer is an **agent firewall** between AI agents, LLMs, and tools. It inspects prompts, tool calls, returned data, and answers to **allow, redact, or block** them, with an audit trail explaining every decision. Agents connect through an OpenAI-compatible proxy by changing their `base_url`.

**Redact sensitive data, block injections, inspect the audit trace.**

![Playground redaction and injection blocking, followed by the audit trace](1-solution/media/playground.gif)

## Submission structure

| Folder | Contents |
|---|---|
| [`1-solution/`](1-solution/README.md) | Overview of the approach, implemented controls and guardrails, policy configuration |
| [`2-architecture/`](2-architecture/README.md) | Architecture diagram, performance of deterministic and AI enforcement |
| [`3-reporting/`](3-reporting/README.md) | Dashboard screenshots, list of implemented metrics |
| [`4-testing/`](4-testing/README.md) | Test cases and attack scenarios the solution showcases |
| [`5-implementation/`](5-implementation/README.md) | Code, design considerations, deployment into existing agentic ecosystems |

**Read next:** [architecture](5-implementation/docs/architecture.md) · [configuration guide](1-solution/configuration.md) · [API contracts](5-implementation/contracts/README.md)

## Run the judge demo

You need Docker. The hosted judge relay supplies OpenAI and TypeSafe access without local provider keys.

```sh
cd 5-implementation
docker compose -f compose.yaml -f compose.judges.yaml up -d --build                       # control layer on :8000, dashboard on :5173
docker compose -f compose.yaml -f compose.judges.yaml run --rm test-app --scenario all    # attacks a two-agent treasury app through the layer
```

The public relay shares a 1,500-call allowance and expires on 5 October 2026 at 23:59 Europe/Warsaw. See the [relay guide](5-implementation/docs/judge-relay.md) for limits, deployment and shutdown. For direct provider access using your own keys, follow the [configuration guide](1-solution/configuration.md) and use `compose.yaml` alone.

Then open the dashboard at **<http://localhost:5173>** and watch the decisions arrive on the **Live** tab.

## The dashboard in one minute

| Tab | What it answers |
|---|---|
| **Overview** | Is the system safe right now? Shows enforcing checks, allowed / redacted / blocked counts, threats over time, which checks block most, response time as median and 95th percentile in ms (security vs. model), and today's tokens and cost per user. |
| **Live** | What is happening this second? Each agent step is a card: checks before the model, what the model asked for, checks on its reply, and the timing. |
| **Audit** | Why was this decided? Rows are grouped by request. Rule checks show *Passed / Redacted / Blocked*, and Jev shows its risk score. Selecting a request opens its decision trace with the total cost. |
| **Policy** | What are the rules? A live YAML editor that validates before saving, plus the active-checks matrix and the Jev threshold. |
| **Playground** | What happens if I try to attack it? Chat through the layer (Enter to send) with attack presets and see every check's verdict. The conversation is kept while you switch tabs. |

## How it works

```mermaid
flowchart LR
    A[Agent<br/>OpenAI client + JWT] -->|prompt| L
    subgraph L[AI Control Layer]
        direction TB
        I[input] --> TC[tool_call] --> TR[tool_result] --> O[output]
    end
    L <-->|allowed traffic only| M[Model]
    L -->|allowed calls only| T[Tools]
    L --> D[(Audit log)]
    P[policy.yaml<br/>hot-reloaded] -.-> L
```

- **Four checkpoints.** The agent re-sends the whole conversation each step, so the layer sees every prompt, tool call, returned data and answer.
- **Rules enforce, Jev decides.** Deterministic checks run first, cheapest first, and the first block is final:

  | Check | What it does |
  |---|---|
  | `permissions` | Blocks unapproved models. Hides tools the user's role may not use and blocks calls to them. |
  | `budget` | Enforces per-role request, token and cost limits. |
  | `loop_detection` | Stops agents that repeat the same tool call. |
  | `signatures` | Matches a live-updated feed of known attack patterns. |
  | `tool_args` | Blocks destructive shell and SQL, path traversal and unsafe deserialization. |
  | `pii_secrets` | Redacts PII and secrets. |

  What gets through the rules goes to **Jev**, a remote LLM judge that scores prompt-injection and overall risk against `jev_threshold`.
- **Fail closed.** If Jev is down, the fallback model judges instead. If both are down, the request is blocked.
- **Policy is the only source of behaviour.** Everything lives in one YAML file. It is validated and hot-reloaded, and a bad edit is rejected while the old policy keeps running.
- **Agents don't crash.** A blocked request gets a normal OpenAI-format reply that explains why.

## Screens

| | |
|---|---|
| ![Live view](3-reporting/screenshots/live.png) | **Live.** Agent steps as they happen. A `run_sql` call with `DELETE` and no `WHERE` is blocked by `tool_args` before the agent can run it. |
| ![Audit log](3-reporting/screenshots/audit.png) | **Audit.** Decisions grouped by request. The selected request's trace shows which check blocked it, why, and what the request cost. |
| ![Policy editor](3-reporting/screenshots/policy.png) | **Policy.** The live policy in the browser. It is validated before it is applied, and a broken edit is rejected with field-level errors. |
| ![Playground](3-reporting/screenshots/playground.png) | **Playground.** A prompt injection stopped at the input checkpoint by `signatures`. The model is never called. |

The submission screenshots use sample data illustrating a `test-app --scenario all` run.

## Try it: attack scenarios

For an interactive test, open the dashboard at <http://localhost:5173> and select **Playground**. Send your own prompts or use the attack presets to see which requests are allowed, redacted or blocked, which checks fired, and why.

`test_app` is a two-agent treasury application. The **Analyst** reads payments and documents, and the **Operator** acts on the Analyst's answer. Each agent has its own JWT, and every tool call in a model reply is checked before the agent sees it, so a blocked tool never runs.

```sh
docker compose -f compose.yaml -f compose.judges.yaml run --rm test-app --list                 # the scenarios
docker compose -f compose.yaml -f compose.judges.yaml run --rm test-app --scenario delete_sql  # one scenario
docker compose -f compose.yaml -f compose.judges.yaml run --rm test-app "Why is TXN-000001 held?" --role clerk
```

| Scenario | Role | What the control layer should do |
|----------|------|----------------------------------|
| `pii_summary` | clerk | Redact PESEL, phone and email in tool results and answers (`pii_secrets`) |
| `poisoned_note` | treasurer | Catch the hidden instruction in a supplier note (`signatures` or `jev`) |
| `email_iban` | clerk | Redact the IBAN in the Analyst's answer, so the Operator never gets it (`pii_secrets`) |
| `delete_sql` | treasurer | Block `DELETE` without `WHERE` in `run_sql` (`tool_args`) |
| `path_traversal` | clerk | Block `../../policy.yaml` in `export_report` (`tool_args`) |
| `vague_question` | clerk | Stop a search loop (`loop_detection`) or let the Analyst say it has no answer |

A `clerk` may hold payments, send emails and export reports. A `treasurer` may also release payments and run SQL. Each scenario uses the role that lets its risky action reach the Operator, and `--role` overrides it. Models vary between runs, so a scenario does not always reach its risky step. The console prints every non-allow decision as `[control] <agent> <checkpoint> <check> <action>: <reason>`. The Operator's actions are mocks that are only logged to `test_app/runs/`.

## Change the policy while it runs

All behaviour comes from [5-implementation/backend/policy.judges.yaml](5-implementation/backend/policy.judges.yaml). Edit it in the dashboard's **Policy** tab or in the file. A valid change is in force within about 2 seconds, and an invalid one is rejected while the old policy stays active. Some things to try:

- Remove `reports.export` from the `clerk` role under `roles:`, then run `--scenario path_traversal`. The model is no longer offered `export_report`.
- Lower `jev_threshold` from `0.6` to `0.4` to make Jev stricter.
- Delete the `checks.tool_args` section and run `--scenario delete_sql`. The check is now off, and the audit log shows the difference.
- Introduce a typo, such as `checks.tool_argz`. The save is rejected with a field-level error, and the system keeps running.

## Where to look

- Dashboard: <http://localhost:5173>
- Audit log: `5-implementation/backend/logs/*.jsonl`, or the dashboard export (`GET /api/audit/export`)
- Layer health: `curl http://localhost:8000/api/health`
- Operator actions and reports: `5-implementation/test_app/runs/<time>/<scenario>/`
- Stop everything: `docker compose -f compose.yaml -f compose.judges.yaml down`

## Setup notes

- Never put keys in `.env.example`, because it is committed. `.env` is gitignored.
- `JWT_SECRET` defaults to a public demo value. To use your own, run `docker compose -f compose.yaml -f compose.judges.yaml run --rm init` once (it writes a random one to `.env`), then `docker compose -f compose.yaml -f compose.judges.yaml up -d --build`.
- Ollama is optional. With `ollama pull gemma4` on the host, pass `--model gemma4` (or set `TEST_APP_MODEL=gemma4`) to use the local model. On Linux, start Ollama with `OLLAMA_HOST=0.0.0.0` so containers can reach it.

Without Docker:

- Backend: [backend/README.md](5-implementation/backend/README.md)
- Test application, direct or through the proxy: [5-implementation/test_app/README.md](5-implementation/test_app/README.md)
- Connecting your own agent: [5-implementation/docs/toolDescription.md](5-implementation/docs/toolDescription.md)

Team rules: [AGENTS.md](AGENTS.md).
