# Feature Specification: Judge API relay

**Feature Branch**: `feat/judge-api-relay`
**Created**: 2026-10-04
**Status**: Ready for implementation; public allowance required before activation
**Input**: Let judges clone and run the project without receiving OpenAI or TypeSafe keys; provision a small cloud function and managed secrets reproducibly and run the demo tests.

## User Scenarios & Testing

### User Story 1 - Run the local demo without provider keys (Priority: P1)

A judge clones the project and uses the real providers through a public relay while the control layer and dashboard run locally.

**Independent Test**: Run the demo with both provider keys absent locally; verify successful model and Jev responses through a test relay.

**Acceptance Scenarios**:
1. Given a configured relay, when a judge runs the project, then neither provider key is required locally.
2. Given a provider outage, when the layer makes a request, then its existing fallback and fail-closed behavior remain effective.
3. Given direct-provider configuration, when a developer supplies their own keys, then the existing workflow still works.

### User Story 2 - Provision and retire the relay from commands (Priority: P2)

The team provisions the relay and secret storage without manually configuring cloud resources.

**Independent Test**: Validate the infrastructure configuration, deploy into the selected project, and exercise both provider routes.

**Acceptance Scenarios**:
1. Given authorized cloud access, when provisioning runs, then it creates a public endpoint and restricted runtime access to the two provider secrets.
2. Given uploaded secrets, when requests are forwarded, then secret values never appear in responses, logs, source files, or infrastructure state.

### User Story 3 - Bound public usage (Priority: P1)

The team limits usage of the public endpoint during judging independently of the editable local policy.

**Independent Test**: Exercise concurrent quota reservations and expired access; verify rejected calls never reach a provider.

**Acceptance Scenarios**:
1. Given exhausted global quota or expired access, when someone sends a request, then forwarding is refused.
2. Given a disallowed route, model, oversized body, or excessive output limit, when someone sends a request, then forwarding is refused.
3. Given multiple instances or restarts, when requests reserve capacity, then global limits remain effective.

### Edge Cases

- Cold starts and provider timeouts must fit the local policy timeouts.
- Provider errors must not disclose credentials or arbitrary upstream error bodies.
- Quota persistence failures must refuse forwarding.
- A public token shipped with the repo cannot establish judge identity.

## Requirements

### Functional Requirements

- **FR-001**: Judges MUST be able to use both providers without local provider credentials.
- **FR-002**: The relay MUST forward only the required fixed provider routes and approved models.
- **FR-003**: The relay MUST store provider credentials privately and replace caller authentication before forwarding.
- **FR-004**: Global usage limits and expiry MUST be enforced remotely and survive restarts.
- **FR-005**: Infrastructure and secret provisioning MUST be reproducible from commands without storing provider keys in infrastructure state.
- **FR-006**: Existing direct-provider and fallback workflows MUST remain supported.
- **FR-007**: Deterministic relay tests, backend checks, and demo tests MUST run; a deployed live demo MUST be verified once the cloud project is supplied.
- **FR-008**: Deployment MUST target the user's Google Cloud project `hackyeah-2026` in `europe-west1` (verify project ID before apply).
- **FR-009**: Public activation MUST require a configured allowance and expiry; keep disabled until supplied.

### Key Entities

- **Relay configuration**: fixed routes, models, body/output limits, expiry, and global allowance.
- **Provider credentials**: the private OpenAI and TypeSafe keys held in managed secret storage.
- **Usage reservation**: persistent capacity reserved before forwarding, including failures.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A fresh clone runs the judge demo with zero local provider keys.
- **SC-002**: All invalid and exhausted-quota test requests make zero provider calls.
- **SC-003**: The test suite proves concurrent requests cannot exceed the configured global allowance.
- **SC-004**: The published handoff contains zero provider credential values.

## Assumptions

- Public capped access is acceptable; judge login is outside the requested scope.
- The cloud project has billing and the user can authorize provisioning.
- Existing credentials may be uploaded securely without printing them.
- Existing design and interfaces remain authoritative: [architecture](../../docs/architecture.md), [contracts](../../contracts/README.md).
