# Implementation Plan: Judge API relay

**Branch**: `feat/judge-api-relay` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

## Summary
Provision an HTTP Cloud Run function with Terraform, Secret Manager credentials, and transactional Firestore quota reservations. Judges run the app locally with a generated policy and Compose override. Preserve direct-provider defaults.

## Technical Context
Python 3.12, Functions Framework, Pydantic v2, httpx, google-cloud-firestore; Terraform Google/archive providers. Project `hackyeah-2026`, region `europe-west1`. Fixed URLs; bounded body bytes, OpenAI output tokens, n=1, no streaming. Persistent lifetime/minute limits and expiry; count failed calls conservatively. Public activation requires allowance and expiry. Upload secret values separately from Terraform.

## Constitution Check
I–IX: the canonical check pipeline stays unchanged; remote transport limits are deployment configuration. Contracts are documented first. Deterministic allow/reject tests and existing fail-closed behavior remain. X: the user's explicit cloud/paid-provider authorization supersedes older no-paid-API scope for this demo. Review passes before and after design.

## Project Structure
- `infra/judge-relay/function/`: deployable function only.
- `infra/judge-relay/*.tf`: infrastructure without secret values.
- `infra/judge-relay/tests/`: validation, forwarding, expiry, quota races.
- `scripts/deploy_judge_relay.py`: staged provisioning and private secret upload.
- `scripts/configure_judge_relay.py`: generate policy and Compose override.
- `backend/app/models/policy.py`, `backend/app/core/jev.py`: explicit keyless Jev support.

## Complexity Tracking
A persistent single Firestore document is necessary because memory counters reset or multiply across instances. Public usage parameters and cloud login remain deployment inputs, not implementation blockers.
