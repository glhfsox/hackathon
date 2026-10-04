# 5. Implementation

Everything that runs lives in this folder. Run all `docker compose` commands from here.

## Code

| Path | What it is |
|---|---|
| [`backend/`](backend/README.md) | The control layer: FastAPI proxy, check pipeline, Jev client, policy store, audit log, dashboard API. Python 3.12, Pydantic v2. |
| [`backend/app/checks/`](backend/app/checks/) | One module per check, all behind the same interface. |
| [`backend/policy.yaml`](backend/policy.yaml) | The policy: the only source of behaviour, commented field by field. |
| [`backend/signatures.yaml`](backend/signatures.yaml) | The attack-signature feed. |
| [`frontend/`](frontend/) | The dashboard: React + Vite + TypeScript. |
| [`test_app/`](test_app/README.md) | Two-agent treasury demo application that attacks the layer. |
| [`demo_data/`](demo_data/README.md) | Synthetic treasury data (clients, payments, documents) for the demo agents. |
| [`contracts/`](contracts/README.md) | API shapes and data models shared by backend and frontend. |
| [`docs/`](docs/architecture.md) | Architecture, tool-setup guide and demo RAG notes. |
| [`specs/`](specs/) | Feature specs, plans and task lists written during the hackathon. |
| `compose.yaml` | Runs the backend, dashboard and test application. |

## Run it

You need Docker and an OpenAI API key.

```sh
cd 5-implementation
cp .env.example .env                               # then put OPENAI_API_KEY (and TYPESAFE_API_KEY) in .env
docker compose up -d --build                       # control layer on :8000, dashboard on :5173
docker compose run --rm test-app --scenario all    # attacks the two-agent app through the layer
```

Without Docker: [backend](backend/README.md), [test application](test_app/README.md). Full configuration: [configuration guide](../1-solution/configuration.md).

## Design considerations

- **Rules enforce, Jev decides.** Rule checks are cheap and deterministic, so they run first and their block is final. Jev cannot override a rule block. It only scores what the rules let through.
- **Jev never sees raw PII.** `pii_secrets` runs before Jev, and the policy refuses `jev` without `pii_secrets`.
- **Fail closed.** A check that errors or times out blocks. If Jev and its fallback are both down, the request is blocked. If the signature feed never loaded, `signatures` blocks.
- **Universal adapter.** Every request is translated into one canonical model, so checks never see vendor JSON. Only the OpenAI adapter is built. Another vendor format is one more adapter.
- **Stateless except the policy and the budget ledger.** The agent re-sends the whole conversation each step, so the layer needs no session store.
- **Known limits.** The layer advises; it does not execute tools. An agent that ignores a blocked reply and runs the tool anyway is not stopped. PII in tool-call arguments is redacted only in what the checks and Jev read, never in the arguments the agent runs. The budget ledger is in memory, so budgets are per instance and reset on restart. The audit log is JSONL files. More in the [backend README](backend/README.md#known-gaps).

## Deploying into an existing agentic ecosystem

1. **Point the agent at the layer.** Any agent built on an OpenAI-compatible client (OpenAI SDK, LangChain, LlamaIndex, CrewAI, AutoGen and similar) changes its `base_url` to `http://<layer>:8000/v1`. No other code changes.
2. **Give it an identity.** The agent sends a JWT signed with HS256 using `JWT_SECRET`, with `sub` (the user), `roles` and `exp`. Your identity provider or agent platform mints it. See [architecture §11](docs/architecture.md).
3. **Describe its tools in the policy.** Add each tool name under `permissions`, and grant permissions to roles under `roles`. Tools a user's roles do not grant are hidden from the model, and calls to them are refused. Step-by-step guide: [`docs/toolDescription.md`](docs/toolDescription.md).
4. **Register its models.** Add each upstream model under `models` with its base URL and API-key env var, and to `checks.permissions.allowed_models`.
5. **Tune and watch.** Set budgets per role and `jev_threshold`, then watch the dashboard. Policy edits apply in about 2 seconds without a restart.
6. **Feed the audit log onward.** The JSONL log and the CSV / JSON export load into a SIEM or Power BI. Optional Langfuse tracing is described in [`backend/docs/observability.md`](backend/docs/observability.md).

Multi-agent systems work the same way: each agent (orchestrator, workers) gets its own token and role, and every hop between agent and model goes through the layer. The demo's Analyst and Operator agents show this.

For production, put the layer behind TLS and protect the dashboard API (`/api`), which has no authentication in this demo.
