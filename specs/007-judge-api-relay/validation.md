# Validation: Judge API relay

Executed 2026-10-04 on the feature worktree based on `origin/main`.

## Passing checks

- Terraform init, validate, fmt, infrastructure apply and live function update completed; final plan reports no changes.
- Backend `ruff check .` and `ruff format --check .`: passed, 120 files formatted.
- Relay/deployment helpers lint/format: passed.
- `pytest infra/judge-relay/tests test_app/tests -q`: **42 passed**. Covers validation, key injection, secret isolation, provider errors, expiry, quota exhaustion, concurrent reservations, and existing scripted treasury agents.
- Targeted backend Jev/demo/policy tests: **154 passed, 3 skipped**.
- Docker build: backend, frontend and test-app images built successfully.
- Public `/health` and `/openai/v1/models`: HTTP 200 without authentication.
- OpenAI live relay completion: HTTP 200, response `Ready.` without a local key.
- TypeSafe live relay verdict: HTTP 200, risk `0.02`, category `benign`, without a local key.
- Isolated Docker backend `/api/health`: HTTP 200, Jev and fallback `up`; dashboard HTTP 200.
- All six live treasury scenarios through the judge Compose override: exit code **0**, no agent errors or limits. IBAN was redacted; path traversal was blocked by `tool_args`; other scenarios completed. The poisoned-note and deletion scenarios did not demonstrate blocks in this run. Model-driven scenario outcomes vary; the runner does not label them pass/fail.
- At the last quota read: **64 / 700** calls consumed, **636** remaining. Quota is persistent; later calls can change this.
- Exact credential-value scan: no provider keys in publishable files or Terraform state.

## Pre-existing failure

Full backend suite before relay edits: **1 failed, 1,342 passed, 3 skipped**.
Full suite after adding keyless Jev tests: **1 failed, 1,344 passed, 3 skipped**.
Both fail only `test_commands_just_under_the_length_limit_stay_linear[tee x ]`: approximately 3.05 seconds against the existing 3-second ceiling. An isolated run outside the sandbox also fails. No shell scanner or timing threshold changes were made for this transport task.

## Deployment

Project `hackyeah-2026-510606`, region `europe-west1`.
Relay URL: `https://judge-api-relay-hoa3z3uclq-ew.a.run.app`.
700 total calls, 60/minute, 32 KiB bodies, 2,048 OpenAI output tokens. Expiry `2026-10-05T21:59:00Z` (23:59 Europe/Warsaw).
Secret values uploaded through stdin outside Terraform. Version IDs pinned in ignored deployment settings.
Existing local stack left running; isolated smoke stack uses ports 18000/15173.

## Tooling limitation

GitHub CLI installation and direct GitHub credential access were declined. A ready PR description is provided alongside the branch for manual PR creation if no authorized PR tool is available.

## Follow-up: allowance and history cleanup

On 2026-10-04, the user increased the lifetime allowance to 1,500. Terraform apply changed only the function configuration, and the deployed `RELAY_SETTINGS` confirms 1,500; all other limits and expiry remain unchanged. The deployment-helper tests passed (6 tests). The initial 700-call validation figures above remain historical results.

`key.txt` was removed from all reachable local and published Git history. Only `dev` and `submission_structure` needed rewriting; all unrelated refs retained their IDs. A fresh remote mirror confirms no `key.txt` paths in any fetched history. Local reflogs and obsolete objects were pruned. Existing clones and GitHub cached views are outside this cleanup; exposed credentials should be rotated.
