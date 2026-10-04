# 1. Solution

## Overview

The AI Control Layer is an **agent firewall**. It sits between AI agents, the LLMs they call and the tools they run. Everything that crosses those boundaries (prompts, tool calls, tool results and final answers) is inspected, and the layer decides to **allow, redact or block** it. Every decision is written to an audit log with its reason, latency and cost.

![Playground redaction and injection blocking, followed by the audit trace](media/playground.gif)

The approach:

- **Drop-in integration.** The layer exposes an OpenAI-compatible `/v1/chat/completions`. An agent changes its `base_url` and sends a signed JWT as its bearer token. No other code changes.
- **Four checkpoints.** `input` → `tool_call` → `tool_result` → `output`. Agents re-send the whole conversation every step, so the layer sees every prompt, action, returned data and answer.
- **Rules enforce, Jev decides.** Deterministic rule checks run first, cheapest first, and a rule block is final. What the rules let through is scored by **Jev**, a remote LLM judge, against a policy threshold.
- **Fail closed.** If Jev is down, a fallback model judges. If both are down, or any check errors, the request is blocked.
- **Policy is the only source of behaviour.** One YAML file, validated on load and hot-reloaded in about 2 seconds. An invalid edit is rejected and the old policy keeps running.
- **Agents don't crash.** A blocked request gets a normal OpenAI-format reply that explains why.

## Implemented controls and guardrails

| Control | Type | Checkpoints | What it does |
|---|---|---|---|
| JWT identity | auth | every request | HS256 token with `sub`, `roles`, `exp`. Missing, expired or forged tokens get 401 and an `auth_failed` audit row. |
| `permissions` | rule | input, tool_call, tool_result | Blocks unapproved models. Hides tools the user's roles may not use from the model, and blocks calls to them (RBAC, deny by default). |
| `budget` | rule | input, tool_result | Per-role requests per minute, tokens per day and cost per day, counted per user. A user with no budgeted role is blocked. |
| `loop_detection` | rule | input, tool_result | Stops runaway agents: too many tool calls in one turn, or the same call repeated (argument order and whitespace normalised). |
| `signatures` | rule | input, tool_call, tool_result | Matches a live-refreshed feed of 20 known attack patterns in five categories: prompt injection, code execution, unsafe deserialization, supply chain and data exfiltration. |
| `tool_args` | rule | tool_call | Blocks destructive shell (`rm -rf`, `shred`, `wipefs`, PowerShell/cmd recursive delete, chaining), destructive SQL (`DROP`, `TRUNCATE`, `DELETE` without `WHERE`), path traversal outside an allowed root, and unsafe deserialization (`pickle`, `yaml.load`, `__reduce__`). |
| `pii_secrets` | rule | input, tool_call, tool_result, output | Redacts email, phone, SSN, PESEL, IBAN, Luhn-valid card numbers and API keys / secrets, then lets the request through. Custom regex patterns can be added in the policy. |
| `jev` | AI | input, tool_call, tool_result, output | Scores prompt injection, hidden instructions in tool results, tool calls that do not serve the task, and overall risk. Blocks when the score is at or above `jev_threshold`. Sees only redacted text. |
| Audit log | reporting | every decision | Append-only JSONL, one row per check result plus one `turn_summary` row per checkpoint. Exportable as JSON or CSV. |

## Configuration: what the policy can enforce

All behaviour lives in [`5-implementation/backend/policy.yaml`](../5-implementation/backend/policy.yaml). It can be edited in the dashboard's **Policy** tab or in the file while the system runs.

| Section | What it controls |
|---|---|
| `version` | Label shown in every audit record, next to a hash of the file. |
| `jev_threshold` | Risk score (0 to 1) at which Jev blocks. Lower is stricter. |
| `models` | Allowed upstream models: base URL, API-key env var, price per 1k tokens, timeout. |
| `permissions` | Permission name → list of tools it grants. |
| `roles` | Role name → list of permissions. Tool access is deny by default. |
| `checks.<id>` | A check is **on** when its section exists and **off** when it is left out. Each section holds that check's parameters (allowed models, budgets per role, loop limits, signature categories, allowed root and categories for tool args, PII types and extra patterns, Jev limits). |
| `signatures` | Where the attack-signature feed lives and how often it is refreshed. |
| `jev` | The judge service, its key env var, timeout and the fallback model. |

Validation rejects unknown check ids, misspelled or mistyped parameters, duplicate keys, and `jev` without `pii_secrets` (so Jev never sees raw PII).

The full field-by-field reference, environment variables and common tasks are in the [configuration guide](configuration.md).
