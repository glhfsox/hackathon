# backend/AGENTS.md — rules for work inside `backend/`

Adds to the root `AGENTS.md`; it does not repeat it. Read before writing code here:
- [`docs/architecture.md`](../docs/architecture.md): request lifecycle, pipeline, Jev fallback, code layout (§10)
- [`contracts/models.md`](../contracts/models.md): Pydantic models mirror it
- [`contracts/http-api.md`](../contracts/http-api.md): endpoints you implement

## Scope

The control layer itself: the OpenAI-compatible proxy, the canonical model and adapter, the checks, the policy loader, the Jev client, the audit log and the API the dashboard uses.

## Rules

- **Contracts are the source.** Pydantic models for shared shapes mirror `contracts/` exactly. If they need to differ, change the contract first, in its own small PR.
- **Vendor JSON stops at the adapter.** Everything after the adapter works only with the canonical model.
- **One check, one module.** It implements the shared check interface, reads only its own policy section and never imports another check.
- **No behaviour constants in code.** Thresholds, patterns, limits and model names come from the policy.
- **The Jev client is the only place that talks to Jev.** It applies the policy timeout, falls back to the local model, and fails closed when neither answers. Every outcome is audited.
- **Every decision is audited, including errors.** Never return a block without writing its audit record.
- **Tests:** every check has at least one allowed and one blocked YAML case. Tests must not call the real Jev; mock it.

## Commands

Run from `backend/`:

```bash
ruff check . && ruff format --check . && pytest
```

The run command and Python version are pinned in `pyproject.toml` when the backend is first scaffolded. Update this section then.
