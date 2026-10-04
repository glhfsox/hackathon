# Feature Specification: Dashboard usability

**Feature Branch**: `feat/dashboard-usability`
**Created**: 2026-10-04
**Status**: Ready for implementation
**Input**: Preserve playground queries across tab changes; send with Enter; remove OWASP and clarify latency and daily usage; separate audit executions; distinguish rule outcomes from AI risk; highlight selected request cost.

## User Scenarios & Testing

### User Story 1 - Continue a conversation (Priority: P1)
Users switch to another dashboard tab and return without losing previous prompts, replies, draft text, selected trace, or an in-progress reply.
**Why this priority**: Losing conversation state interrupts the main demo workflow.
**Independent Test**: Send a prompt, switch tabs, return, and send a follow-up.
**Acceptance Scenarios**:
1. Given a completed prompt and a draft, when the user returns from Audit, then both remain and the follow-up includes allowed conversation history.
2. Given a pending reply, when the user switches tabs, then its completion is available on return.
3. Given a prompt, when Enter is pressed, then it sends; Shift+Enter adds a newline; composing text does not send.
4. Given a blocked or failed turn, when a follow-up is sent after navigation, then that turn is visible but excluded from forwarded history.

### User Story 2 - Understand the overview (Priority: P2)
Users read standalone response times and daily usage.
**Why this priority**: Unexplained abbreviations and ratios obscure the system's behavior.
**Independent Test**: Open Overview with representative metrics and identify units, percentile meanings, usage, and policy coverage.
**Acceptance Scenarios**:
1. The Overview does not show an OWASP table.
2. Response time has separate median and 95th percentile values, milliseconds, and plain explanations without relative bars.
3. Daily usage shows tokens and monetary cost; unknown limits do not appear as infinity.

### User Story 3 - Read audit decisions (Priority: P2)
Users distinguish executions before selection, read rule outcomes, and immediately spot cost in a selected trace.
**Why this priority**: Execution boundaries and decision semantics must be visible during a demo.
**Independent Test**: Load interleaved requests and select one with recorded usage.
**Acceptance Scenarios**:
1. All loaded checks of a request appear together under a visible execution heading; system events remain separate.
2. Deterministic checks show categorical outcomes, while AI checks retain numeric risk and score bars in Audit and Playground.
3. Selected traces highlight request cost and checkpoint costs without double-counting check rows.

### Edge Cases
- Zero usage is shown as zero; unavailable budget limits are explained.
- Only loaded audit rows contribute to the displayed cost, with that scope labeled.
- Requests ending in a block remain readable after navigation.
- Empty audit and metric datasets retain useful empty states.

## Requirements

### Functional Requirements
- **FR-001**: Preserve the playground session during dashboard tab navigation, including pending responses.
- **FR-002**: Send on Enter, add newlines on Shift+Enter, and respect text composition.
- **FR-003**: Remove the OWASP table from Overview.
- **FR-004**: Present latency independently with units and understandable percentiles.
- **FR-005**: Show daily tokens and cost without implying unknown limits are unlimited.
- **FR-006**: Visibly group audit checks by request before selection.
- **FR-007**: Use categorical deterministic outcomes and preserve AI score bars.
- **FR-008**: Highlight recorded request and checkpoint cost in the selected audit trace.

### Key Entities
- Conversation: visible turns, safe forwarded history, current draft, and selected trace.
- Execution: loaded audit rows sharing a request identifier.
- Metrics: existing overview response fields; see [HTTP API](../../contracts/http-api.md).

## Success Criteria

### Measurable Outcomes
- **SC-001**: All completed and pending turns survive a round trip to another tab during the same page session.
- **SC-002**: One Enter press sends one prompt; Shift+Enter sends none.
- **SC-003**: The OWASP table is absent; every response-time value has units; unknown budgets never display infinity.
- **SC-004**: Every loaded execution has a visible boundary and deterministic rows contain no numeric risk score.
- **SC-005**: Selecting a request displays its recorded cost in a distinct highlighted element.

## Assumptions
- Preservation applies to tab navigation within the dashboard, not browser reloads or durable chat storage.
- Shift+Enter is the multiline input gesture.
- Remove OWASP coverage presentation as requested; no security behavior changes.
- Execution grouping uses request identifiers, consistent with current decision trace selection.
- Existing contracts and backend security behavior remain unchanged.
