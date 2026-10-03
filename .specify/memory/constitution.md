<!--
Sync Impact Report
- Version change: template (unversioned) → 1.0.0 (initial ratification)
- Principles defined:
  I. Policy Is the Single Source of Behaviour
  II. Contracts First, Every Fact in One Place
  III. Uniform, Isolated Checks
  IV. Cheap Before Expensive
  V. Rules Enforce, Jev Decides
  VI. Fail Closed, Explain Every Block
  VII. Everything Is Audited
  VIII. Transparent Integration
  IX. Every Check Tested Both Ways
  X. Working Demo Over Breadth
- Added sections: Scope Constraints, Development Workflow, Governance
- Removed sections: none
- Templates: plan/spec/tasks templates read this file at runtime; not modified by this command.
- Deferred TODOs: none
-->

# AI Control Layer Constitution

## Core Principles

### I. Policy Is the Single Source of Behaviour

- Every check, action, threshold, permission, budget, and strictness profile MUST come from the
  one policy document. Nothing that changes a decision may be hard-coded.
- A policy MUST be validated before it becomes active. An invalid policy MUST be rejected with a
  clear error, and the previously active policy MUST stay in force.
- Policy changes MUST take effect on a running system without a restart, and each request MUST
  be evaluated against exactly one policy version (never a mix).

Rationale: judges edit the policy live; behaviour that is not in the policy cannot be controlled.

### II. Contracts First, Every Fact in One Place

- Shared interfaces (canonical request, check result, decision, policy schema, audit record,
  HTTP API) MUST be defined in the contracts location before parallel work builds on them.
- Every fact MUST live in exactly one place: rules in AGENTS.md, design in the architecture
  document, interface shapes in contracts, principles here. Specs and plans MUST link, not copy.
- A contract change MUST be a small, dedicated, announced change.

Rationale: four people and several coding agents work in parallel; duplicated facts drift.

### III. Uniform, Isolated Checks

- Every check, rule-based or AI-based, MUST take the canonical request plus its own policy
  section and return a decision of `allow | redact | block | flag` with a score, a reason, and
  its latency.
- Checks MUST NOT see vendor-specific formats, MUST NOT call each other, and MUST be addable or
  removable without changing other checks.

Rationale: uniformity makes checks testable, reorderable, and policy-controllable.

### IV. Cheap Before Expensive

- Checks MUST run in ascending order of cost, and the first `block` MUST stop the pipeline.
- The AI decision maker MUST be consulted only after the rule-based checks for that checkpoint.

Rationale: keeps overhead low and avoids spending AI calls on requests already rejected.

### V. Rules Enforce, Jev Decides

- Deterministic rule checks are hard limits. A rule `block` is final.
- Jev, a remote LLM, is the AI decision maker. It judges what the rules let through and returns a
  risk score that MUST be compared against a policy-defined threshold.
- Jev MAY tighten a decision but MUST NOT override a rule `block`.
- If Jev is unavailable, the decision MUST fall back to the policy-configured local model. If no
  AI decision maker is available, Principle VI applies.
- Data sent to Jev MUST already be redacted by the rule checks, so secrets and personal data never
  leave the system.

Rationale: rules give guarantees; the AI catches what rules cannot express; neither failure may
open a hole.

### VI. Fail Closed, Explain Every Block

- A check that errors, times out, or is unreachable MUST result in `block` when the check runs in
  block mode, or `flag` in monitor mode. It MUST NOT silently allow.
- Every block MUST return a well-formed, protocol-compatible response that names the check and
  the reason, so calling agents do not crash.

Rationale: a security layer that fails open is worse than none.

### VII. Everything Is Audited

- Every decision at every checkpoint MUST be recorded append-only with caller, checkpoint, check,
  action, reason, score, latency, and cost (including which AI decision maker answered).
- Audit records MUST NOT be edited or deleted, and the log MUST be exportable.

Rationale: resilience and traceability are the product; unrecorded decisions cannot be trusted.

### VIII. Transparent Integration

- An agent MUST be protectable by changing only its model endpoint and API key, with no code
  changes. Caller identity MUST come from the API key.
- Tool execution MUST also be guardable at a second entry point, so an agent that ignores a
  verdict is still stopped.

Rationale: zero-friction adoption is what makes the layer usable by any agent.

### IX. Every Check Tested Both Ways

- Every check MUST have at least one allowed case and one blocked case, expressed as data-driven
  test cases. A check without both is not done.
- The full suite MUST run with one command.

Rationale: judges run the tests; both directions prove the check works and does not over-block.

### X. Working Demo Over Breadth

- A narrow feature that works end-to-end MUST be preferred over a broad unfinished one.
- Any work outside the agreed scope MUST be approved by the team before it starts.
- No paid APIs may be used.

Rationale: 24 hours, four people; only the working control layer is scored.

## Scope Constraints

- In scope: the control layer; one tiny demo agent with tools designed to trigger the checks
  (fake customer data with PII, a shell/code executor); a dashboard with policy editor and chat
  playground; the automated test suite.
- Out of scope: a multi-agent SDK or orchestrator, agent-topology configuration, RAG, adapters
  beyond the first protocol, and authentication beyond API keys.

## Development Workflow

- Team rules (git flow, branches, definition of done, code principles, agent conduct) live in
  AGENTS.md and are not repeated here.
- Feature specs map user stories to independent feature branches; shared contracts and the core
  pipeline merge first.
- Every plan MUST include a Constitution Check against Principles I–X.

## Governance

- This constitution supersedes other practices. Where AGENTS.md and this document conflict on a
  principle, this document wins; AGENTS.md is updated to match.
- Amendments are made by PR that updates this file, bumps the version, and appends a line to the
  decisions section of AGENTS.md.
- Versioning is semantic: MAJOR for removed or redefined principles, MINOR for new principles or
  materially expanded guidance, PATCH for clarifications and wording.
- Every plan and PR MUST be checked for compliance. A deviation MUST be justified in the plan's
  Complexity Tracking section or rejected.

**Version**: 1.0.0 | **Ratified**: 2026-10-03 | **Last Amended**: 2026-10-03
