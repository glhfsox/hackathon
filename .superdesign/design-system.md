# Design System — Aegis AI Control Layer (dark SecOps)

## Product context
AI Control Layer: a gateway that sits between AI agents, LLMs, tools and MCP servers. For every interaction it decides **allow, redact, block or flag**. It runs rule checks (PII/secrets, permissions, tool-argument validation, attack-signature feed, budgets, loop detection) and an AI judge (prompt injection, hidden instructions). The policy is one YAML file with profiles `strict | balanced | permissive`, reloaded live.

Users:
- **Security analyst (SOC)**: investigates blocked threats, filters the audit log, drills into one request's decision trace, exports evidence.
- **Manager**: wants security posture, threat trend and AI spend at a glance.
- **Operator / judge**: edits the policy live and tries attack prompts in the playground.

## Key pages
1. **Overview dashboard**: posture score, KPI tiles (requests, allowed, redacted, blocked, flagged), blocked threats over time, blocks by check, budget usage per caller, latency p50/p95 per check, OWASP LLM Top 10 coverage, live threat feed, system health (judge up/down, fallback, signature feed freshness, policy version).
2. **Audit log**: dense filterable table (time, request id, caller, model, checkpoint, check, action, score, latency, cost), filter bar, JSON/CSV export, side drawer with the full decision trace of a request.
3. **Policy editor**: YAML code editor with line numbers, profile switcher, validate and save, field-level validation errors panel, version history.
4. **Playground**: chat with an agent through the layer; each message shows the checks that ran per checkpoint (input, tool_call, tool_result, output) with action and score chips; redacted spans highlighted.

## Visual direction
Professional security operations centre, premium and slightly cinematic, never toy-like or neon-gamer. Think high-end SIEM / threat-intel console: deep layered blacks, hairline borders, crisp data, subtle glow only on live or critical states.

## Colour tokens
- `--bg-0` #07090D (app background)
- `--bg-1` #0C1017 (panels)
- `--bg-2` #121823 (raised cards, table header)
- `--bg-3` #1A2230 (hover, inputs)
- `--border` #1F2937 (hairline 1px), `--border-strong` #2B3648
- `--text-1` #E6EDF5, `--text-2` #9AA7B8, `--text-3` #5F6B7C
- Brand / primary: cyan `--accent` #22D3EE, `--accent-dim` #0E7490 (links, focus rings, primary buttons, active nav)
- Actions (semantic, used consistently everywhere):
  - allow: emerald #10B981
  - redact: amber #F59E0B
  - block: crimson #F43F5E
  - flag / monitor: violet #A78BFA
- Severity scale: critical #F43F5E, high #FB7185, medium #F59E0B, low #38BDF8, info #64748B
- Charts: series use cyan, violet, emerald, amber, rose on dark; gridlines #1F2937; no rainbow.
- Glow: only `0 0 0 1px` coloured ring + soft `0 0 24px` of the semantic colour at 15–20% opacity on live/critical items.
- Background detail: very subtle dotted grid or radial vignette in page header only.

## Typography
- UI: **Inter** (400/500/600). Numbers use tabular figures.
- Data, ids, code, YAML, scores, latencies: **JetBrains Mono** (400/500).
- Sizes: page title 22/600, section title 14/600 uppercase tracking 0.08em in `--text-2`, body 13–14, table 12.5, KPI numbers 28–32/600.

## Layout
- Desktop 1440 wide. Left sidebar 240px (logo "Aegis" wordmark with shield glyph, nav: Overview, Audit log, Policy, Playground; bottom: active profile pill + system health dots).
- Top bar 56px: page title, environment badge `LOCAL`, time-range selector (15m / 1h / 24h / 7d), live indicator (pulsing emerald dot "Live"), policy version chip.
- Content grid 12 columns, 24px gutter, cards radius 10px, 1px border, no heavy drop shadows.
- Density: SOC-dense but breathable; tables 36px rows.

## Components
- KPI tile: label (uppercase small), big mono number, delta vs previous period, micro sparkline.
- Action chip: rounded 6px, 10% tinted background + 1px border in action colour + text in action colour, e.g. `BLOCK`, `REDACT`, `ALLOW`, `FLAG`.
- Check chip: mono text `pii_secrets`, neutral.
- Score bar: thin 4px bar 0–1, colour by severity.
- Buttons: primary cyan fill with dark text; secondary ghost with border; destructive crimson outline.
- Inputs: `--bg-3`, 1px border, cyan focus ring.
- Tables: sticky header `--bg-2`, zebra off, row hover `--bg-3`, left 2px colour bar by action.
- Code editor: `--bg-0` panel, line numbers `--text-3`, YAML syntax colours from the palette, error lines with crimson gutter marker.

## Motion
Subtle only: 150ms ease-out hover, live dot pulse 2s, new audit rows fade in from cyan tint. No bouncing, no parallax.

## Rules
- Use ONLY the fonts, colours, spacing and component styles defined here.
- Semantic action colours never change meaning between pages.
- Realistic data: caller ids (`support-bot`, `analyst-agent`, `playground`), checks (`permissions`, `budget`, `loop_detection`, `signatures`, `tool_args`, `pii_secrets`, `jev`), models (`llama3.2:3b`, `qwen2.5:7b`), CVE ids (CVE-2024-37032, CVE-2023-48022, CVE-2023-29374).
