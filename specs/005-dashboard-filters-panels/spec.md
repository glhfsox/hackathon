# Feature Specification: Dashboard filters and resizable panels

**Feature Branch**: `feat/dashboard-filters-panels`
**Created**: 2026-10-03
**Status**: Ready for implementation
**Input**: Share an editable time filter across Overview and Audit, and resize the sidebar, traces, and Playground prompt like editor panels. Implement on a separate branch from dev and test it.

## User Scenarios & Testing

### User Story 1 — Choose a shared time window (Priority: P1)

An operator selects a clearly highlighted preset or enters a custom duration and sees the same window in Overview, Audit, and audit downloads.

**Why this priority**: The current global control only affects Overview and its selected state is visually unclear.
**Independent Test**: Select 1h, navigate to Audit, and inspect requests and export links. Apply 45m and verify both pages refresh. An invalid duration must leave the previous window active.

**Acceptance Scenarios**:
1. Selecting each preset visibly changes the active choice and refreshes the displayed data.
2. Entering a positive whole duration with m/h/d and applying it updates both pages and exports.
3. Switching pages preserves the applied window and caller/check/action/checkpoint filters remain usable.
4. Invalid input explains the accepted format without fetching an invalid window; late responses cannot overwrite newer selections.

### User Story 2 — Resize the workspace (Priority: P1)

An operator gives navigation, Audit trace, Playground decision trace/raw trace, and prompt composer more or less space by dragging their shared boundaries.

**Why this priority**: Fixed widths and a single-line prompt prevent comfortable inspection and editing.
**Independent Test**: Drag every separator and confirm the relevant panel changes size, stays usable at its limits, and can also be resized with a keyboard.

**Acceptance Scenarios**:
1. Resize navigation and Audit trace horizontally.
2. Resize the Playground trace column horizontally and decision/raw trace split vertically.
3. Resize the multiline prompt vertically without losing the draft or submitting it; send with its button or Ctrl/Cmd+Enter.
4. Keyboard arrows resize focused separators; minimum sizes preserve usable neighboring panels.

### Edge Cases

- Empty/zero/negative/malformed/overflowing duration; rapid filter changes and slow responses.
- Narrow viewports, pointer cancellation, dragging outside the divider, repeated page switches.
- Long prompt/trace content must scroll inside its panel instead of hiding controls.

## Requirements

- **FR-001**: Share one relative time-window selection across Overview and Audit and include it in audit exports.
- **FR-002**: Provide visibly active presets, labeled editable duration, Apply action, and accessible validation feedback. Show it only on pages it affects.
- **FR-003**: Ignore obsolete polling responses after the active query changes.
- **FR-004**: Supply discoverable pointer and keyboard resize handles for all named panels, constrained by available space.
- **FR-005**: Support multiline prompt editing, explicit Send, and Ctrl/Cmd+Enter without changing middleware decisions or conversation safety.
- **FR-006**: Verify the interactions in a browser as well as lint and production build; document exact results.

## Success Criteria

- Every valid applied window appears in requests and export links on both supported pages.
- Every named panel changes size by pointer and keyboard without overlapping or disappearing.
- Invalid input preserves the previous active window and delayed responses cannot restore old data.
- The automated interaction checks, lint, and build pass with a runnable manual test guide.

## Assumptions

- “Movable” means resizing shared boundaries, consistent with the user's editor analogy; rearranging panels is outside this feature.
- Retain the terminal theme and existing backend API defined in [contracts/http-api.md](../../contracts/http-api.md).
- Use the explicitly requested dev base rather than the repository's usual main base.
