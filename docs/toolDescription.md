# Tool description: setting up the policy for your agent's tools

This guide is for teams who put their own agent behind the AI Control Layer. It explains how tools are declared, how the proxy recognises them, and which part of `policy.yaml` you need to write. The field-by-field reference for the file is in the comments of [`backend/policy.yaml`](../backend/policy.yaml). The endpoints are in [`contracts/http-api.md`](../contracts/http-api.md).

## 1. Where a tool is defined

A tool is defined in two places. Each place has its own job.

| Place | What you write there | Who reads it |
|-------|----------------------|--------------|
| Your agent's OpenAI `tools` array | The name, the description and the JSON Schema of the arguments | The model. The proxy forwards the array unchanged. |
| `policy.yaml`, `callers.<id>.allowed_tools` | Only the tool **name** | The proxy. It decides who may call the tool. |

The policy does not store tool descriptions or argument schemas. It only needs the name. That name must be identical in both places. The match is exact and case-sensitive: `send_email` and `sendEmail` are different tools.

A tool that is not in the caller's `allowed_tools` is blocked at the `tool_call` checkpoint by the `permissions` check, with the reason `tool '<name>' is not allowed for role '<role>'`.

## 2. Writing a tool so the checks understand it

The `tool_args` check reads every string inside the tool-call arguments, including nested objects and lists. Some rules depend on the **names** you choose:

| If the tool name or argument name contains… | The value is treated as… | Extra rules that apply |
|---|---|---|
| `shell`, `bash`, `sh`, `zsh`, `cmd`, `command`, `commands`, `cmdline`, `terminal` | a shell command | Command chaining (`;`, `&&`, `\|\|`, `&`, newline), substitution (`$(`, backticks), `sudo`, and overwrite redirects are blocked. Each command is limited to `max_command_chars`. |
| `path`, `paths`, `file`, `files`, `filename`, `filepath`, `dir`, `directory`, `folder`, `cwd` (argument name) | a file path | The path must stay inside `allowed_root`. A relative path counts as inside. |

These rules apply to every argument, whatever its name:

- `..` segments, URL-encoded traversal and well-known sensitive files are blocked.
- Destructive shell primitives are blocked, for example `rm -rf`, `curl … | sh` and reverse shells.
- SQL `DELETE` or `UPDATE` without a real `WHERE` clause is blocked. A `WHERE 1=1` counts as no `WHERE`.
- Unsafe deserialization (pickle and similar loaders) is blocked.

Recommendations:

- Name a file argument `path` or `file`, not `target` or `location`. Otherwise only the traversal rules apply to it, not the `allowed_root` rule.
- Name a shell argument `cmd` or `command`, or put `shell` in the tool name. Otherwise chaining such as `a; b` is not blocked.
- Keep descriptions honest. If a tool can write, say so. Jev, the AI check, judges the conversation, and the tool schemas are part of the model's context.
- Use `additionalProperties: false` in the argument schema. The proxy does not validate your schema; your agent should reject unknown arguments itself.

## 3. Which checks see a tool

| Checkpoint | What crosses it | Checks that look at tools |
|------------|-----------------|---------------------------|
| `tool_call` | The model asks to run a tool | `permissions` (the name is allowed), `tool_args` (arguments are safe), `signatures` (attack patterns in the arguments) |
| `tool_result` | The tool output goes back to the model | `pii_secrets` (redacts PII and secrets), `signatures` and `jev` (hidden instructions in returned data), `loop_detection` (same call repeated, or too many calls), `budget`, `permissions` (model) |

The proxy only judges. Your agent must call the tool guard, `POST /v1/tools/check`, before it executes any tool. It must run the tool only when the guard answers `"allowed": true`. Then an agent that ignores a refusal in a chat reply still cannot execute the blocked call.

## 4. Steps to set up the YAML

1. **Declare the model** under `models`. The key is the `model` value your agent sends.
2. **Create one caller per agent** under `callers`. Give each agent its own API key. Then each agent gets its own permissions, budget and audit trail.
3. **Point `api_key_env` at an environment variable name**, never at the key itself. If that variable is unset or empty, the caller is disabled.
4. **List the tool names** in `allowed_tools`. Give each agent only the tools it needs. A read-only agent must not get write tools.
5. **Set `budgets`.** Leave a limit out for no limit.
6. **Check `checks.tool_args`.** Set `allowed_root` to the folder your file tools may use, and keep the categories you need.
7. **Validate before saving.** Send the file to `POST /api/policy/validate`, or edit it in the dashboard. An invalid file is rejected, and the previous policy stays active.
8. **Set the variables and run.** Export the key variables, point your agent's `base_url` at the proxy (`http://<host>:8000/v1`), and use the caller's key as the Bearer token.

Errors you are likely to see when validating:

| Message | Cause |
|---------|-------|
| `model is not defined in models` | `allowed_models` names a model that is missing from `models` |
| `unknown key for tool_args: …` | A misspelled parameter. Unknown keys are errors, not ignored. |
| `tool_args never runs at input; it runs at: tool_call` | A mode was set for a checkpoint that this check does not use |
| `Extra inputs are not permitted` | A misspelled field in a caller, for example `allowed_tool` |

## 5. Tool reference: the test application

These are the tools of the two-agent test application ([`test_app/`](../test_app/README.md)). Use them as an example for your own entries.

### Agent A, Analyst (read-only)

| Tool | Arguments | What it does | Risk the proxy covers |
|------|-----------|--------------|------------------------|
| `search_documents` | `query` (string, 1..8000 chars), `k` (integer 1..10, default 5), `transaction_id` (`TXN-NNNNNN` or null) | Searches the client's treasury documents and returns the matching sections, with citations. | Returned text can hold PII (`pii_secrets` at `tool_result`) or hidden instructions (`signatures`, `jev` at `tool_result`). Repeated identical searches trigger `loop_detection`. |
| `query_transactions` | `transaction_id` (`TXN-NNNNNN` or null), `status` (`settled`, `pending`, `held` or null), `limit` (integer 1..100, default 20) | Reads exact payment records and per-currency totals for the client. No free SQL. | Returned records hold account data (`pii_secrets` at `tool_result`). |

The client scope is set by the operator outside the tool arguments, so the model cannot change it.

### Agent B, Operator (acts on the Analyst's answer)

| Tool | Arguments | What it does | Risk the proxy covers |
|------|-----------|--------------|------------------------|
| `send_email` | `to`, `subject`, `body` (strings) | Sends an email. A mock in the test app. | An IBAN, PESEL or card number in `body` can leak to an outside address. |
| `release_payment` | `transaction_id` (`TXN-NNNNNN`) | Releases a held payment. A mock in the test app. | A poisoned document can make the Analyst recommend a release. `jev` judges this. No rule limits the amount. |
| `hold_payment` | `transaction_id` (`TXN-NNNNNN`) | Puts a payment on hold. A mock in the test app. | Low risk. |
| `export_report` | `path` (string, relative to the reports folder), `content` (string) | Saves a report as a text file. | Path traversal such as `../../policy.yaml` is blocked by `tool_args` (path_traversal). `path` is a path argument, so `allowed_root` applies. |
| `run_sql` | `query` (string) | Read-only SQL by description. It only logs the query in the test app. | `DELETE` or `UPDATE` without a real `WHERE` is blocked by `tool_args` (sql). A `DELETE` with a real `WHERE` passes, so give a read-only tool a read-only database role as well. |

### Policy entries for the test application

Add these under the existing top-level keys of `policy.yaml`. The `gemma4` model is already declared in the shipped file.

```yaml
callers:
  analyst:                        # Agent A: read-only, never writes or pays
    api_key_env: ANALYST_API_KEY
    role: analyst
    allowed_models: [gemma4]
    allowed_tools: [search_documents, query_transactions]
    budgets: {requests_per_minute: 30, tokens_per_day: 100000, cost_per_day: 0.5}
  operator:                       # Agent B: acts, so it gets no read tools and a smaller budget
    api_key_env: OPERATOR_API_KEY
    role: operator
    allowed_models: [gemma4]
    allowed_tools: [send_email, release_payment, hold_payment, export_report, run_sql]
    budgets: {requests_per_minute: 20, tokens_per_day: 50000, cost_per_day: 0.25}

checks:
  tool_args:
    tool_call: block
    allowed_root: "/workspace"    # export_report paths must stay inside this folder
    categories: [shell, sql, path_traversal, deserialization]
    max_command_chars: 2000
```

Then set the keys in the environment before you start the proxy and the agents:

```sh
export ANALYST_API_KEY=<random secret>
export OPERATOR_API_KEY=<another random secret>
```

With this policy:

- The Analyst calling `release_payment` is blocked by `permissions`.
- The Operator calling `run_sql` with `DELETE FROM transactions` is blocked by `tool_args`, with the reason `run_sql.query: sql: DELETE without WHERE`.
- `export_report` with `../../policy.yaml` is blocked by `tool_args`, with the reason `export_report.path: path_traversal: '..' segment`.

## 6. Template for your own tool

Copy this block for every tool you add. Fill it in before you write the policy entry.

```markdown
### <tool_name>

- **Agent / caller:** <caller id in policy.yaml>
- **Purpose:** <one sentence: what it does>
- **Side effects:** none | writes files | sends data out | moves money | runs commands
- **Arguments:** <name (type, limits)>, …   — name file arguments `path`/`file`, shell arguments `cmd`/`command`
- **Returns:** <what comes back to the model; does it contain PII or third-party text?>
- **Risks:** <what an honest mistake or a poisoned input could make it do>
- **Checks that cover it:** permissions, tool_args (<categories>), pii_secrets / signatures / jev at tool_result
- **Policy entry:** listed in `callers.<id>.allowed_tools`
```
