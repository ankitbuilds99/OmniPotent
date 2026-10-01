# Console content baseline

Captured before the light dashboard redesign.

- Route: `/console`, rendered by `frontend/src/App.tsx`.
- Header actions: OmniPotent home, New conversation, Sign up/Logout, History, Team, Status, Dashboard, Billing, Admin, configured model label, API health indicator, theme picker, and profile/avatar.
- Left rail: configured model/provider and main/fast model IDs; `ModelConfig`; MCP server count; each configured server's label, honest live/demo/local/unknown badge, tool chips, and connection controls for Datadog, repository, and GitHub.
- Center empty state: sparkles icon, “What can I help investigate?”, the existing analysis subtitle, model configuration tip, four `SUGGESTIONS`, and the real `Composer`.
- Center active state: real `useCopilot()` conversation turns rendered by `Message`, approval controls, retry, feedback, streaming/busy state, and the same `Composer` dock.
- Composer: incident prompt input, saved-query controls, Cmd/Ctrl+Enter submit, newline on Enter, stop while busy, and Send.
- Overlays: Admin panel and signup modal.
- Data integrity: no recent-investigation dataset is currently loaded by `Console`; integration health comes from `/integrations/status` and API health from `/health`.

