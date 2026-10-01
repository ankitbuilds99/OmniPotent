# Console behavior baseline

Captured before the light dashboard redesign.

- `Console` owns the live `useCopilot()` session. Suggestions call the real `send()` function; active conversations preserve approval, retry, feedback, stop, and auto-scroll behavior.
- Header navigation delegates to the existing route callbacks supplied by `App`; Admin and signup open existing overlays; theme selection persists through `useTheme`.
- The sidebar reads shared config, polls integration status once on mount, and renders connection/source controls without claiming an unverified connector is live.
- The top-bar health dot polls `/health` every 10 seconds.
- Composer uses the existing keyboard and busy-state semantics; saved queries remain local to the current user/session via `useSavedQueries`.
- Existing theme architecture is global (`html[data-theme]`) with Cosmic as the default and Light as an option. The redesign must retain theme switching and not alter landing-page routes.
- Existing responsive behavior hides the sidebar at small widths; the redesign needs an accessible mobile drawer and must avoid horizontal overflow while keeping the conversation usable.
