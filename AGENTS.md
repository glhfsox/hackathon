# AGENTS.md — shared rules for everyone working in this repo

Read this first. It applies to every human and every AI agent (Claude Code, GitHub Copilot, Codex, anything else). If it conflicts with your tool's defaults, this file wins. If it is wrong or outdated, fix it in a small PR instead of working around it.

## 1. Project

**AI Control Layer** (HackYeah 2026, Goldman Sachs task, 24 h, team of four). It is middleware that secures interactions between AI agents, models and tools. For everything that crosses those boundaries it decides to **allow, redact or block**. Only the control layer is scored. The agents that use it are demo clients. Design: [`docs/architecture.md`](5-implementation/docs/architecture.md). Data shapes and endpoints: [`contracts/`](5-implementation/contracts/README.md).

- **Rules enforce, Jev decides.** Deterministic rule checks are hard limits, and a rule block is final. **Jev** is the AI decision maker, a remote (non-local) LLM. Jev scores what the rules let through, and the score is compared against a policy threshold. If Jev is unavailable, the request goes to local Ollama model
- **Policy is the only source of behaviour.** It is one YAML file, validated on load and hot-reloaded. An invalid edit is rejected and the old policy stays active. Nothing is hard-coded. Judges will edit the policy while the system runs.
- **Fail closed and explain every block.** Every decision is audited.
- Design details not written down in `docs/` or `contracts/` are undecided. Ask your human instead of inventing them. Ask before expanding scope.

## 2. How it works

Summary only. If this section and [`docs/architecture.md`](5-implementation/docs/architecture.md) disagree, the architecture doc wins.

- **Integration:** the layer exposes an OpenAI-compatible `/v1/chat/completions`. An agent switches its `base_url` and sends a signed JWT as its bearer token, and is protected with no other code changes. The user and their roles come from the token, and the policy maps roles to tools (see architecture §11). An agent that ignores our verdict and runs a blocked tool anyway is not stopped by the layer.
- **Checkpoints:** input → tool-call (proxy reply) → tool-result → output. The agent re-sends the whole conversation every step, so the layer sees every prompt, action, returned data and answer.
- **Universal adapter:** every request is translated into one canonical internal model, and checks never see vendor JSON. Only the OpenAI adapter is built. Other formats are "one more adapter" by design.
- **Checks:** all checks share one interface. They receive the canonical request plus their policy settings and return `allow | redact | block | flag` with a score, reason and latency. They run cheap before expensive and stop at the first block.
  - Rule checks:
    - PII and secrets redaction
    - role-based tool permissions: tools the user's roles do not allow are hidden from the model, and denied tool calls are replaced by a notice
    - tool-argument validation (shell, destructive SQL, path traversal, unsafe deserialization)
    - an external attack-signature feed
    - loop detection
  - AI check: Jev (prompt injection, hidden instructions in tool results, overall risk score).
- **Policy:** controls which checks are on (a check is on when its section exists), their parameters, the Jev threshold, models, which roles may use which tools, and the budgets per role. There are no modes or profiles: a finding blocks, `pii_secrets` redacts and lets the request through, and an error blocks.
- **Blocked requests** return a normal OpenAI-format refusal with the reason, so agents don't crash.
- **Reporting:**
  - The append-only audit log stores every decision with its reason, latency and cost, and it is exportable.
  - The dashboard shows security posture, blocked threats, usage per user and overhead. It also has a policy editor and a chat playground that shows which checks fired.
- **Tests:** data-driven YAML cases, run with one command. Every check has at least one allowed and one blocked case.
- **Scope:**
  - We build: the layer, one tiny demo agent (a fake customer DB with PII, plus a shell/code tool), the dashboard and the tests.
  - We do not build: a multi-agent SDK/orchestrator, RAG, non-OpenAI adapters, or auth beyond verifying signed tokens.

## 3. Stack

- **Backend:** Python, FastAPI, Pydantic v2. `ruff` for lint/format, `pytest` for tests.
- **Frontend:** React (Vite) + TypeScript, deliberately simple, with no state-management library.
- **AI decision maker:** Jev (remote) if available . Ollama as a fallback. No paid APIs. 
- **Contract:** the backend and frontend build to `contracts/`. The frontend talks to the backend through a single API client module.

## 4. Repository layout and context

```
AGENTS.md            Rules for every agent (this file). CLAUDE.md = "@AGENTS.md".
.specify/            spec-kit: memory/constitution.md (principles only, no tech),
                     templates/, scripts/, integration.json.
.claude/, .github/   spec-kit commands for Claude Code and Copilot.
1-solution/ … 4-testing/  Submission write-ups for the judges. They link into 5-implementation.
5-implementation/    Everything that runs. Paths below are relative to it.
  compose.yaml       Docker entry point; run `docker compose` from this folder.
  docs/architecture.md The single description of stack, data model and design.
  contracts/         API shapes and schemas. The one source of truth for backend and frontend.
  specs/NNN-<name>/  One folder per feature: spec.md (what/why), plan.md (how), tasks.md.
  backend/           Backend code + its own AGENTS.md and CLAUDE.md (@AGENTS.md).
  frontend/          Frontend code + its own AGENTS.md and CLAUDE.md (@AGENTS.md).
  test_app/, demo_data/  Demo agents and their synthetic data.
```

- The root `AGENTS.md`/`CLAUDE.md` load automatically. The `backend/` and `frontend/` rule files load only when working in that folder.
- `docs/`, `contracts/`, `specs/` and `.specify/` are never auto-loaded. **Read `docs/` and `contracts/` before writing code.**
- **Every fact lives in exactly one place.** Rules go in AGENTS.md, design in `docs/`, API shapes in `contracts/`, principles in the constitution. Specs link to these and never copy them.

## 5. Working together

| Zone | Paths | Owner |
|------|-------|-------|
| Contracts + design (shared) | `5-implementation/contracts/`, `5-implementation/docs/` | everyone, small announced PRs only |
| Backend core | `5-implementation/backend/` | TBD |
| Frontend / UI | `5-implementation/frontend/` | TBD |
| Demo agent + tests | TBD | TBD |

Zones are a hint, not a lock. Before editing outside your zone, `git fetch` and check open branches/PRs on the same files. If someone else is changing them, tell your human. Agents coordinate only through git and humans. Keep cross-zone edits small, and never reformat files you do not own.

### Git

- Never push to `main`. Create one branch per feature from fresh `origin/main`: `feat/ fix/ chore/ docs/<slug>`. Keep branches short-lived (hours).
- Use Conventional Commits in English, imperative mood, with a subject of at most 72 chars. Keep commits small and push often.
- No mandatory review. The author squash-merges their own PR after checks pass, then deletes the branch.
- Merge `origin/main` into your branch (don't rebase) before opening and before merging the PR. Force-push only your own branch, with `--force-with-lease`.
- Agents commit and push to their task's branch and open a PR. They merge only when their human says so.

```bash
git fetch origin && git checkout -b feat/<slug> origin/main
git push -u origin feat/<slug>
git fetch origin && git merge origin/main && gh pr create --fill
gh pr merge --squash --delete-branch   # when told to merge
```

### Definition of done

- Backend: `ruff check . && ruff format --check . && pytest` pass. Frontend: `npm run lint && npm run build` pass.
- You actually ran the thing and saw it work.
- The PR says what changed, why, and anything teammates must do (env var, dependency, migration).

## 6. Code principles

1. **Simplicity first.** Write the minimum code that works and can be demoed. No speculative features or abstractions.
2. **Surgical changes.** Every changed line traces to the task. Mention unrelated problems in the PR instead of fixing them.
3. **Match the surrounding code** in naming, structure and comment density.
4. **Validate at trust boundaries.** Use Pydantic for everything entering the backend (HTTP, policy, model output, Jev responses).
5. **Explicit errors.** No bare or swallowed exceptions. Log with enough context to debug after the fact.
6. **Types.** Type hints across module boundaries. TS `strict`, and no `any` without a comment.
7. **Tests scaled to the work.** Deterministic logic (checks, parsing, policy) gets pytest tests. Glue and UI get a smoke check.
8. **Dependencies** only when they clearly save time. Say so in the PR, and put lockfile changes in their own commit.
9. **Secrets** are never committed. Keep a `.env.example` with variable names only.
10. **Comments** explain *why*, in English. No commented-out code.

## 7. For AI agents

- Read this file at session start. Re-read the zones before touching shared files.
- Use the spec-kit commands (`/speckit-*`) for feature work.
- For anything non-trivial, state a brief plan with verification steps before coding.
- Ask when a requirement is ambiguous or undecided. Never silently pick an interpretation.
- Report what you actually ran and what it printed. Never claim an unrun check passed.
- No destructive git commands (`reset --hard`, `clean -fd`, force-push, branch deletion) unless your human asked for exactly that.
- Prefer targeted edits over wholesale rewrites.
- Record team-wide decisions in section 8, not only in chat.

## 8. Decisions and open questions

Append-only, newest at the bottom: `YYYY-MM-DD — decision or question (who)`. On a merge conflict, keep both sides.

- 2026-10-03 — Stack: FastAPI backend + simple React frontend. Feature branches, PR to `main`, no mandatory review. All code, comments and commits in English.
- 2026-10-03 — OPEN: what `jev` is, in one sentence, so every agent shares the same definition.
- 2026-10-03 — OPEN: demo deadline and what the demo must show.
- 2026-10-03 — Project is the AI Control Layer middleware (PROJECT_CONTEXT_1.md); multi-agent session tracing dropped.
- 2026-10-03 — Jev = remote LLM decision maker (closes the "what jev is" question); rules enforce, Jev cannot override a rule block; Jev unavailable → fail closed.
- 2026-10-03 — Repo layout + "every fact in one place" rule adopted (section 4).
- 2026-10-03 — OPEN: Jev endpoint, auth, request/response format, cost.
- 2026-10-03 — Jev unavailable → local Ollama fallback; both unavailable → fail closed (supersedes the "fail closed" part above).
- 2026-10-03 — Draft v0.1 of `docs/architecture.md` and `contracts/` (models, HTTP API) added. OPEN: policy schema (`contracts/policy.example.yaml`).
- 2026-10-03 — User requested a RAG demo-client extension: implement synthetic financial source-data generation and document retrieval design now; embeddings, vector search, and agent implementation deferred. Supersedes the RAG exclusion for this explicitly requested demo work only (spdsslg).
- 2026-10-03 — Demo source data moves to PostgreSQL; remove the corpus SQLite integration. Keep JSONL/Markdown fixtures and evaluator-only files. Next step uses BGE-M3 directly in Python for embeddings, then pgvector; chunking/embedding/search remain deferred during this migration (spdsslg).
- 2026-10-03 — User authorizes the remaining treasury demo pipeline: structure-based Markdown chunking with a bounded paragraph fallback, direct BGE-M3 embeddings, exact pgvector search, fixed read-only transaction tools, and a guarded agent client. Keep it small; live Ollama/backend connection is deferred and documented in docs/rag-handoff.md (spdsslg).
- 2026-10-03 — Jev wire format closed: TypeSafe System One (`POST {base_url}/v1/systemone`, typed `noul`/`choice` answers; score = P(risky)). Key via `TYPESAFE_API_KEY`; no key or failure → local Ollama fallback → fail closed. Only `backend/app/jev.py` talks to it. (Artem)
- 2026-10-03 — Mode table change (architecture §4): a `redact` verdict in `block` mode now blocks, so PII gives block / redact / flag under strict / balanced / permissive. (Artem)
- 2026-10-03 — `permissions` (model) and `budget` also run at `tool_result`: a request ending in a tool message must not skip them (architecture §4 table updated). (Artem)
- 2026-10-03 — `signatures` and `jev` inspect the whole forwarded conversation, all roles. A finding in history keeps blocking while the agent re-sends it; the playground must drop a blocked message from its history. (Artem)
- 2026-10-03 — Signature feed unreachable at policy load: the policy is accepted and the last good feed stays; if no feed ever loaded, the `signatures` check fails closed (closes the spec edge case). (Artem)
- 2026-10-03 — AuditRecord gained optional agent/economic fields and a `turn_summary` row per checkpoint (`contracts/models.md`); tokens and cost live only on that row. Free metrics for judges come from `logs/*.jsonl` (Power BI); Langfuse is self-hosted and optional, for the presentation only. (Artem)
- 2026-10-03 — Policy validation rejects unknown check ids, checkpoints a check never runs at, unknown or mistyped params, and `jev` enabled where `pii_secrets` is off. Provisional schema lives in `backend/app/policy.py`; `backend/policy.yaml` is the documented example. (Artem)
- 2026-10-03 — Demo: two agents on the bare OpenAI client (worker with Python tools behind the tool guard, orchestrator that delegates via a `delegate` tool call), in `backend/demo/`, with a local stand-in gateway until the real proxy lands; the proxy calls `app.pipeline.run_checkpoint`. (Artem)
- 2026-10-03 — API-key auth, the tool guard (`POST /v1/tools/check`) and the policy `callers` section are removed. Every request is caller_id `anonymous` (`ANONYMOUS_CALLER` in `backend/app/core/proxy.py`) until JWT-based identity lands (teammate). `permissions` (`allowed_models`, `allowed_tools`) and `budget` (`requests_per_minute`, `tokens_per_day`, `cost_per_day`) are global parameters of their check sections. Supersedes the API-key and tool-guard parts of the entries above; constitution 2.0.0. (Artem)
- 2026-10-03 — Modes and profiles are removed. A check is on when its `checks.<id>` section exists and then runs at every checkpoint it applies to; a finding blocks, `pii_secrets` redacts and continues, an error blocks (fail closed). One top-level `jev_threshold` replaces the profiles. `/api/metrics` reports `jev_threshold` instead of `active_profile`; `enabled_checks` keeps its per-checkpoint shape. Constitution 2.1.0. (Artem)
- 2026-10-03 — `jev` and `pii_secrets` also run at `tool_call`. There they read the tool-call view (task, agent reasoning, calls; `CanonicalRequest.tool_call_view`), which `pii_secrets` redacts before Jev sees it; the agent's arguments are never changed. Rule checks run first, so a rule block stops a call before Jev is asked. (Artem)
- 2026-10-03 — Identity is a signed JWT (HS256 only, `sub`, `roles`, `exp` required, secret in the `JWT_SECRET` env var), replacing API keys and the policy `callers` section. Any failure is 401 and an `auth_failed` audit row. (Oleh)
- 2026-10-03 — Tool access is role-based: policy `permissions` (permission → tools) and `roles` (role → permissions), deny by default. Inbound the model is not shown forbidden tools, outbound a forbidden tool call is replaced by a text notice, and every tool decision is an `rbac` audit row (architecture §11). OpenAI format only. (Oleh)
- 2026-10-03 — Dropped: budgets, per-caller model lists, the `budget` check and the usage ledger (they need shared storage). Cost reporting stays. `permissions` now runs only at `tool_call`; this supersedes the entry that put `permissions` (model) and `budget` at `tool_result`. (Oleh)
- 2026-10-03 — OPEN: how the playground and the demo agents obtain a token (the layer has no token endpoint). The tool guard may be removed. Tests and the demo agents still assume API keys and need migrating. (Oleh)
- 2026-10-03 — Merged `feature/authorization` into the refactor: JWT identity and RBAC (Oleh) are kept; the tool guard, modes and profiles stay removed. `permissions` checks the model (`checks.permissions.allowed_models`) at input/tool_result and the role's tools at tool_call. Budgets are back, per role (`checks.budget.roles.<role>`), counted per user (`sub`) in the in-memory ledger: a user gets the most generous limits of their roles, and a user none of whose roles has a budget is blocked. Supersedes the "dropped budgets" and "anonymous caller" entries above. The demo agents mint their own tokens with `JWT_SECRET`. Constitution 2.2.0. (Artem)
- 2026-10-03 — Judges run everything with root `compose.yaml` (`init`, `up`, `run test-app`); Ollama stays on the host and a socat relay keeps `localhost:11434` in `policy.yaml` valid inside Docker. `test_app` switches to proxy mode with `TEST_APP_PROXY_URL`; user roles `clerk`/`treasurer` map to callers `operator_clerk`/`operator_treasurer`. (Sviatoslav)
- 2026-10-03 — `docker compose up` alone runs the stack: `JWT_SECRET` defaults to a public demo value in `compose.yaml` (override via `.env` or the shell; `init` writes a random one), and the frontend image mints the playground's JWT (`sub` playground, role `developer`, 30 days) from it at build time. Ollama stays on the host. `test_app` signs its own tokens (`analyst`; `operator_<role>` with role `clerk`/`treasurer`) and no longer calls the tool guard; the three old callers became RBAC roles with budgets. (Artem)
- 2026-10-03 — The demo runs on OpenAI: `gpt-4o-mini` is the agents' model and Jev's fallback, keyed by `OPENAI_API_KEY` through the new `api_key_env` of `models.<name>` and `jev.fallback` (an empty key env fails closed). `gemma4` on Ollama stays in the policy for offline use; tests point the fallback at mocked Ollama and never call OpenAI. Supersedes "Jev unavailable → local Ollama fallback" and the "no paid APIs" line in §3 for the demo. (Artem)

- 2026-10-04 — User authorizes a Terraform-managed public judge relay in Google Cloud project `hackyeah-2026-510606`, `europe-west1`, with Secret Manager keys uploaded outside Terraform state. Judges run the control layer locally without provider keys. Remote allowance: 700 lifetime provider calls, 60/minute, 32 KiB request bodies, 2,048 OpenAI output tokens, expiry 2026-10-05 23:59 Europe/Warsaw. `jev.api_key_env: null` explicitly permits keyless relay transport; direct-provider defaults stay authenticated. (Mihail)

- 2026-10-04 — User increases the public judge relay allowance to 1,500 lifetime provider calls. Other limits and expiry remain unchanged. User authorizes removal of `key.txt` from file tracking and all Git history. (Mihail)

- 2026-10-04 — User requests the submission_structure layout on feat/judge-api-relay: submission write-ups in folders 1–4; runtime code, Compose files, relay infrastructure and scripts in 5-implementation. Run deployment and Compose commands from that folder. (Mihail)

- 2026-10-04 — User requests a direct main update raising the public judge relay rate limit to 200 provider calls per minute. The 1,500-call lifetime allowance and expiry remain unchanged. (Mihail)
