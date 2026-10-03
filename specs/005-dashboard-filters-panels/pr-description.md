# Fix shared dashboard filters and add resizable workspace panels

The footer time filter affected only Overview, and its selected color was overridden by button styling. It now has visibly selected presets and an editable duration with validation, shared by Overview, Audit, and filtered CSV/JSON exports. Obsolete polling responses cannot overwrite a newly selected window.

Navigation, Audit trace, Playground trace/JSON panels, and the prompt composer now have pointer and keyboard resize dividers with bounded sizes and double-click reset. The prompt supports multiple lines, explicit Send, and Ctrl/Cmd+Enter.

Validation: six Chrome interaction tests pass, along with lint, production build, and strict test/config type checking. Tests cover time-window requests/exports, validation, stale responses, resizing, and prompt submission. Live UI/backend verification was not run because backend startup was declined.

Teammates: run `npm ci` for the new Playwright development dependency, then `npm test`. Tests default to installed Google Chrome; set `PLAYWRIGHT_BROWSER=chromium` after installing Playwright Chromium to use it instead. No runtime dependencies, migrations, env-variable requirements, policy changes, or backend API changes. Panel reordering and resize persistence after unmount/reload are outside this feature.

Base: dev, explicitly requested by the user. Do not merge without their instruction.
