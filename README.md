# AI Control Layer

**A security checkpoint between AI agents and everything they touch.** Change one URL, and every prompt, tool call, tool result and answer is checked. The layer decides to **allow, redact or block**, gives a reason, and writes an audit record.

```python
client = OpenAI(base_url="http://localhost:8000/v1", api_key=user_jwt)  # the whole integration
```

![Dashboard overview: security posture, request counts, threats over time, blocks by check, recent threats and response time](docs/screenshots/overview.png)

**Read next:** [the pitch](docs/pitch.md) · [architecture](docs/architecture.md) · [configuration guide](docs/configuration.md) · [API contracts](contracts/README.md)

## Run it in 3 commands

You need Docker and an OpenAI API key. The agents' model (`gpt-4o-mini`) and Jev's fallback run at OpenAI.

```sh
cp .env.example .env                               # then put OPENAI_API_KEY (and TYPESAFE_API_KEY) in .env
docker compose up -d --build                       # control layer on :8000, dashboard on :5173
docker compose run --rm test-app --scenario all    # attacks a two-agent treasury app through the layer
```

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
| ![Live view](docs/screenshots/live.png) | **Live.** Agent steps as they happen. A `run_sql` call with `DELETE` and no `WHERE` is blocked by `tool_args` before the agent can run it. |
| ![Audit log](docs/screenshots/audit.png) | **Audit.** Decisions grouped by request. The selected request's trace shows which check blocked it, why, and what the request cost. |
| ![Policy editor](docs/screenshots/policy.png) | **Policy.** The live policy in the browser. It is validated before it is applied, and a broken edit is rejected with field-level errors. |
| ![Playground](docs/screenshots/playground.png) | **Playground.** A prompt injection stopped at the input checkpoint by `signatures`. The model is never called. |

The screenshots use sample data that mirrors a `test-app --scenario all` run. To regenerate them, see [Screenshots](#screenshots).

## Try it: attack scenarios

`test_app` is a two-agent treasury application. The **Analyst** reads payments and documents, and the **Operator** acts on the Analyst's answer. Each agent has its own JWT, and every tool call in a model reply is checked before the agent sees it, so a blocked tool never runs.

```sh
docker compose run --rm test-app --list                 # the scenarios
docker compose run --rm test-app --scenario delete_sql  # one scenario
docker compose run --rm test-app "Why is TXN-000001 held?" --role clerk
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

All behaviour comes from [backend/policy.yaml](backend/policy.yaml). Edit it in the dashboard's **Policy** tab or in the file. A valid change is in force within about 2 seconds, and an invalid one is rejected while the old policy stays active. Some things to try:

- Remove `reports.export` from the `clerk` role under `roles:`, then run `--scenario path_traversal`. The model is no longer offered `export_report`.
- Lower `jev_threshold` from `0.6` to `0.4` to make Jev stricter.
- Delete the `checks.tool_args` section and run `--scenario delete_sql`. The check is now off, and the audit log shows the difference.
- Introduce a typo, such as `checks.tool_argz`. The save is rejected with a field-level error, and the system keeps running.

## Where to look

- Dashboard: <http://localhost:5173>
- Audit log: `backend/logs/*.jsonl`, or the dashboard export (`GET /api/audit/export`)
- Layer health: `curl http://localhost:8000/api/health`
- Operator actions and reports: `test_app/runs/<time>/<scenario>/`
- Stop everything: `docker compose down`

## Setup notes

- Never put keys in `.env.example`, because it is committed. `.env` is gitignored.
- `JWT_SECRET` defaults to a public demo value. To use your own, run `docker compose run --rm init` once (it writes a random one to `.env`), then `docker compose up -d --build`.
- Ollama is optional. With `ollama pull gemma4` on the host, pass `--model gemma4` (or set `TEST_APP_MODEL=gemma4`) to use the local model. On Linux, start Ollama with `OLLAMA_HOST=0.0.0.0` so containers can reach it.

Without Docker:

- Backend: [backend/README.md](backend/README.md)
- Test application, direct or through the proxy: [test_app/README.md](test_app/README.md)
- Connecting your own agent: [docs/toolDescription.md](docs/toolDescription.md)

## Screenshots

[`frontend/tests/screenshots.spec.ts`](frontend/tests/screenshots.spec.ts) writes the images in `docs/screenshots/`. Run it from `frontend/`:

```sh
npm ci
SCREENSHOTS=1 VITE_PLAYGROUND_API_KEY=demo VITE_PLAYGROUND_MODEL=gpt-4o-mini npx playwright test screenshots  # sample data
SCREENSHOTS=live npx playwright test screenshots                                                            # the running stack
```

Playwright uses Chrome by default. Set `PLAYWRIGHT_BROWSER=msedge` to use Edge instead.

Team rules: [AGENTS.md](AGENTS.md).
