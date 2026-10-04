# Tasks: Judge API relay

## Setup
- [x] T001 Create feature artifacts in `specs/007-judge-api-relay/` and fresh worktree.
- [x] T002 Document transport in `contracts/relay.md` and design in `docs/judge-relay.md`.

## US1: Run locally without keys
- [x] T003 [US1] Support keyless Jev in `backend/app/models/policy.py`, `backend/app/core/jev.py`, with tests in `backend/tests/test_jev_client.py`.
- [x] T004 [US1] Implement validated fixed routes in `infra/judge-relay/function/main.py` and tests in `infra/judge-relay/tests/test_relay.py`.
- [x] T005 [US1] Generate judge policy/Compose override in `scripts/configure_judge_relay.py` with tests.

## US3: Bound public usage
- [x] T006 [US3] Implement atomic reservations and expiry in `infra/judge-relay/function/main.py` with concurrency/failure tests.

## US2: Provision from commands
- [x] T007 [US2] Define function, identities, APIs, storage, secrets and Firestore in `infra/judge-relay/*.tf`.
- [x] T008 [US2] Implement staged deployment/private uploads in `scripts/deploy_judge_relay.py`.
- [x] T009 [US2] Authenticate, validate and deploy `infra/judge-relay/` with user allowance.

## Verification and handoff
- [x] T010 Run backend checks/offline demos; record in `specs/007-judge-api-relay/validation.md`.
- [x] T011 Run deployed smoke/treasury demos; record in `specs/007-judge-api-relay/validation.md`.
- [ ] T012 Update `README.md`, commit, push and create PR.

Dependencies: setup → transport/limits → infrastructure/deployment → live demo → handoff. Research and baseline checks may run concurrently. Validate offline, provision disabled, activate with explicit limits.
