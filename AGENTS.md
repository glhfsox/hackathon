# AGENTS.md — shared context for everyone working in this repo

Read this first. It applies to every human and every AI agent (Claude Code, Codex, Cursor, Copilot, anything else) on the team. If something here conflicts with your tool's defaults, this file wins. If it is wrong or outdated, fix it in a small PR instead of working around it.

## 1. Project

A resilient middleware that verifies the correctness of decisions and traces long-running autonomous multi-agent sessions, with **jev** acting as the decision maker. The middleware is agnostic to the goals of the agent system it wraps. Agents are configured through a UI.

- Hackathon project, team of four, each person runs their own agents.
- Most design details are still open. Do not invent them: if a task depends on an undecided detail, say so and ask the human. In particular, do not guess what *jev* is or how it works.
- Demo deadline / scope: **TBD** (fill in).

Design consequences that are already settled:

- **Goal-agnostic core.** No domain-specific logic or hardcoded agent goals in the core. Anything goal-specific is configuration or a plugin.
- **Configuration is data.** Agent configs come from the UI, so they are validated at the boundary and never trusted as code.
- **Resilience and traceability are the product**, not extras. Failures must be recorded and surfaced, never swallowed silently.

## 2. Stack

- **Backend:** Python, FastAPI, Pydantic v2. Lint/format with `ruff`, tests with `pytest`. Pin the Python version in `pyproject.toml` when scaffolding.
- **Frontend:** React (Vite) + TypeScript, deliberately simple. No state-management library until a concrete need appears.
- **Contract:** FastAPI's OpenAPI schema is the source of truth for the HTTP API. The frontend talks to the backend through a single API client module, so contract changes touch one place.

Proposed layout (whoever scaffolds first keeps this section in sync):

```
backend/      FastAPI app, core logic, tests
frontend/     React app
docs/         extra notes, only when needed
AGENTS.md     this file
```

## 3. Working together

### Zones

Each person owns a rough zone. Zones are a hint to reduce collisions, not a lock, and they will shift as the project moves. Changing a zone is a one-line edit to this table.

| Zone | Paths | Owner |
|------|-------|-------|
| Backend core | `backend/` | TBD |
| API / contract | `backend/api/` (proposed) | TBD |
| Frontend / UI | `frontend/` | TBD |

Rules for crossing zones:

1. Before editing outside your zone, check what is in flight: `git fetch` and look at open branches/PRs touching the same files.
2. If someone else is changing the same files, tell your human so they can sync with the teammate. Agents do not talk to each other directly; coordination goes through git and the humans.
3. Keep cross-zone edits small and focused. Never reformat or restructure files you do not own.

### Git

- **Never push to `main` directly.** All work goes through feature branches and PRs.
- **Branch per feature**, created from fresh `origin/main`: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`. Slugs are short, lowercase, hyphenated.
- **Keep branches short-lived** (hours, not days). Long branches are what cause painful conflicts.
- **Commits:** English, imperative, [Conventional Commits](https://www.conventionalcommits.org/) style (`feat: add run timeline endpoint`), subject at most 72 chars. Small, focused commits. Push to your branch often.
- **No mandatory review.** The author merges their own PR, but only after the checks below pass. Squash merge, then delete the branch.
- **Keep your branch current** by merging `origin/main` into it (not rebasing), so no force-push is needed. Do this before opening the PR and before merging.
- Force-push only your own feature branch and only with `--force-with-lease`. Never force-push `main`.
- **Agents** commit and push to the feature branch of their current task. They open a PR, and merge only when their human says so.

Typical flow:

```bash
git fetch origin && git checkout -b feat/<slug> origin/main
# work, commit small, push
git push -u origin feat/<slug>
git fetch origin && git merge origin/main      # before the PR
gh pr create --fill
gh pr merge --squash --delete-branch           # after checks pass, when told to merge
```

### Definition of done (before any merge)

- Backend: `ruff check . && ruff format --check . && pytest` pass.
- Frontend: `npm run lint && npm run build` pass.
- You actually ran the thing and saw it work, not only the tests.
- The PR description says what changed and why, plus anything teammates must do (new env var, new dependency, migration).

If a command above does not exist yet, creating it is part of the first task that needs it.

## 4. Code principles

1. **Simplicity first.** Write the minimum code that solves the problem. No speculative features, no abstraction for single-use code, no configurability nobody asked for. For a hackathon, working and demoable beats elegant.
2. **Surgical changes.** Every changed line should trace to the task. Do not "improve" neighbouring code, rename things, or reformat files you did not need to touch. Clean up only your own mess. Mention unrelated problems in the PR instead of fixing them.
3. **Match the surrounding code.** Follow existing naming, structure and comment density, even if you would do it differently.
4. **Validate at trust boundaries.** Pydantic models for everything entering the backend (HTTP, UI-provided agent configs, model output). Do not add defensive checks for states that cannot occur.
5. **Explicit errors.** No bare `except`, no swallowed exceptions. Fail loudly with a clear message, and log with enough context to debug a long session after the fact.
6. **Types.** Type hints on all backend functions that cross a module boundary. TypeScript `strict`, no `any` without a comment explaining why.
7. **Tests scaled to the work.** Deterministic logic (parsing, validation, routing, decision checks) gets pytest tests. Glue code and UI get a smoke check. No coverage targets.
8. **Dependencies.** Add one only when it clearly saves time, and say so in the PR. Lockfile changes are conflict magnets, so keep them in their own commit.
9. **Secrets.** Never commit keys, tokens or `.env`. Keep a `.env.example` with variable names only.
10. **Comments** explain *why*, not *what*. English only. No commented-out code.

## 5. For AI agents specifically

- Read this file at the start of every session. Re-read the Zones table before touching shared files.
- For anything beyond a trivial change, state a brief plan (steps, and how you will verify each) before coding.
- Ask the human when a requirement is ambiguous or touches an undecided design point. Do not silently pick one interpretation.
- Report what you actually ran and what it printed. Never claim a check passed without running it.
- Do not run destructive git commands (`reset --hard`, `clean -fd`, force-push, branch deletion) unless your human asked for that exact thing.
- Do not rewrite files wholesale when a targeted edit works.
- Record anything the whole team must know (a decision, a changed contract, a gotcha) in section 6, not only in chat.

## 6. Decisions and open questions

Append-only, newest at the bottom, one line each: `YYYY-MM-DD — decision or question (who)`. Merge conflicts here are trivial, so keep both sides.

- 2026-10-03 — Stack: FastAPI backend + simple React frontend. Feature branches, PR to `main`, no mandatory review. All code, comments and commits in English.
- 2026-10-03 — OPEN: what `jev` is, in one sentence, so every agent shares the same definition.
- 2026-10-03 — OPEN: demo deadline and what the demo must show.
