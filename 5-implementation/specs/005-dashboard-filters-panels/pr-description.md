# Fix shared dashboard filters and add resizable workspace panels

The footer time filter affected only Overview, and its selected color was overridden by button styling. It now has visibly selected presets and an editable duration with validation, shared by Overview and Audit. Obsolete polling responses cannot overwrite a newly selected window.

Audit trace, Playground trace/JSON panels, and the prompt composer now have pointer and keyboard resize dividers with bounded sizes and double-click reset. The prompt supports multiple lines, explicit Send, and Ctrl/Cmd+Enter.

The redundant single-directory sidebar is removed. A larger branded header keeps the four navigation tabs, while the clock, NORMAL badge and status path are removed. Playground empty states explain the prompt-to-decision flow. Section titles and actions use plain labels, without terminal ornaments or decorative square brackets; the editable time filter remains available. The Audit field-filter/export toolbar is removed, and the four tabs use plain names without numbered prefixes.

Validation: six Chrome interaction tests pass, along with lint, production build, and strict test/config type checking. Tests cover time-window requests, toolbar removal, validation, stale responses, resizing, and prompt submission. Live UI/backend verification was not run because backend startup was declined.

Teammates: run `npm ci` for the new Playwright development dependency, then `npm test`. Tests default to installed Google Chrome; set `PLAYWRIGHT_BROWSER=chromium` after installing Playwright Chromium to use it instead. No runtime dependencies, migrations, env-variable requirements, policy changes, or backend API changes. Panel reordering and resize persistence after unmount/reload are outside this feature.

Base: dev, explicitly requested by the user. Do not merge without their instruction.
