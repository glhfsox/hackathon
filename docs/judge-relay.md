# Judge API relay

Judges run the control layer, policy editor, dashboard and treasury agents locally. Only provider transport runs in Google Cloud. The public relay adds private credentials and forwards to fixed OpenAI/TypeSafe endpoints. Provider keys never enter the checkout, responses, or Terraform variables/state. Transport and configuration are defined in [contracts/relay.md](../contracts/relay.md).

## Provision without console setup

Install Terraform, Google Cloud CLI, and Python 3.12. Sign in with an identity allowed to enable APIs, create service accounts and IAM grants, create storage/Firestore/secrets, and deploy Cloud Functions. Billing must be enabled on the existing project.

```sh
gcloud auth login --update-adc
python3 -m pip install pyyaml
python3 scripts/deploy_judge_relay.py --project hackyeah-2026-510606 \
  --region europe-west1 --env-file .env --total-calls 1500 \
  --expires-at 2026-10-05T21:59:00Z --enable
```

The helper validates credentials without displaying them, applies infrastructure, uploads each key directly to Secret Manager through stdin, pins the returned numeric versions, deploys the function and generates `backend/policy.judges.yaml` plus `compose.judges.yaml`. Running without `--enable` deploys a disabled relay. Repeated deploys preserve quota history and the existing function. Secret values come from environment variables or the local gitignored `.env` file.

Terraform creates API enablement, separate build/runtime identities, a private source bucket, two regional secrets, a named Firestore database, the function and public invocation permission. Secrets are accessible only to the runtime identity through secret-specific grants. Firestore access is scoped to the relay database. The database is retained by Terraform teardown so allowance history is not silently reset; decommission it explicitly when no longer needed.

Terraform state and `deployment.auto.tfvars.json` stay local and gitignored. Preserve the state directory for subsequent updates; losing state requires import rather than redeploying existing resources blindly. Share state with teammates through private storage if more than one person operates this deployment.

## Judge run

The published judge files contain only public URLs and policy settings. After they are committed, judges need Docker and internet access:

```sh
docker compose -f compose.yaml -f compose.judges.yaml up -d --build
docker compose -f compose.yaml -f compose.judges.yaml run --rm test-app --scenario all
```

Open <http://localhost:5173>. The override explicitly blanks both provider key variables, even if a local `.env` contains them. The generated policy remains hot-reloaded and editable through the dashboard. Its relay timeouts account for cold starts; existing direct-provider `compose.yaml` and `backend/policy.yaml` stay available.

For another relay deployment, generate the files with:

```sh
python3 scripts/configure_judge_relay.py https://YOUR-RELAY-URL
```

## Public access limits

This endpoint does not distinguish judges from other users. Its remote limits are independent of the editable local policy: 1500 lifetime provider calls, 60 calls per minute across all instances, 32 KiB bodies, one OpenAI completion with at most 2,048 output tokens, and expiry October 5, 2026 at 23:59 Europe/Warsaw. Provisioning smoke/demo calls consume that allowance too.

A Firestore transaction reserves capacity before each provider call. Failures retain reservations because upstream usage may already have occurred. A storage failure refuses forwarding; instance restarts and overlapping deployments preserve the counters. Unsupported models/routes, oversized bodies, streaming, and client-provided upstream URLs are refused.

These limits bound provider usage, not exact dollars. Public HTTP traffic, logging and Firestore reads can still incur GCP charges, including after provider allowance is exhausted. Expiry stops provider calls; it does not remove cloud resources.

To stop provider calls early, set `enabled` to `false` in the ignored `infra/judge-relay/deployment.auto.tfvars.json`, then:

```sh
terraform -chdir=infra/judge-relay apply
```

This updates the existing function. Do not remove the variables file when applying: the bare Terraform defaults deliberately omit the function for bootstrap. Full teardown is a separate deliberate action; the Firestore database is retained.

## Verification

```sh
python3 -m pip install -r infra/judge-relay/function/requirements.txt pytest
python3 -m pytest infra/judge-relay/tests test_app/tests -q
terraform -chdir=infra/judge-relay init
terraform -chdir=infra/judge-relay validate
terraform -chdir=infra/judge-relay fmt -check
```

Backend verification follows [backend/AGENTS.md](../backend/AGENTS.md). Live verification uses the judge Compose commands above and the public `/health` route. See [feature validation](../specs/007-judge-api-relay/validation.md) for actually executed results.
