# OmniPotent landing behavior baseline

Captured before the visual refactor on 2026-10-01 at commit `d8646e5b7755bb80daaf9f588ae6f0f65273ffdd`.

## Routing and boundaries

- `/marketing` renders `CinematicLanding` from `frontend/src/App.tsx`.
- `/` and `/landing` redirect to `/console`; this redesign must not change that behavior.
- The redesign scope is limited to the public landing route. Dashboard, console, authentication, onboarding, billing, and other routes are out of scope.
- `CinematicLanding` passes `onLaunch` to navigation, hero, AI chat, pricing, and footer. Its demo action stores `demo_user` and calls `onDashboard`.

## Navigation

- Desktop links remain `Features`, `Pipeline`, `Docs`, `Blog`, `Pricing`, and `API`.
- Hash destinations remain `#features`, `#pipeline`, `#docs`, `#blog`, and `#pricing`.
- API opens `${API_BASE_URL}/docs` in a new tab.
- Theme selection uses `THEMES`, `DEFAULT_THEME`, `data-theme`, and localStorage key `copilot-theme`.
- Existing actions remain `Sign in` and `Launch Console`.
- Mobile navigation exposes all existing links and the Launch Console action.

## Existing demo and data behavior

- Hero rotating words cycle every 2500ms: incidents, outages, alerts, failures, pages.
- Hero MCP/tool statistics come from `useCatalog()` and render only when the catalog answers; the LLM provider count remains 5.
- Hero integration names and Simple Icons sources remain unchanged.
- HowItWorks retains step selection, typewriter/demo state, approval controls, play/pause, reset/replay, and launch action.
- Terminal retains exact terminal content and its Replay control.
- FeatureCards retains all cards, expanded/detail behavior, and existing motion/tilt behavior.
- Pipeline retains all stages, logs, progress, selection, and restart behavior.
- GlobalMap was removed from the public marketing composition by explicit
  approval; its former deep-link/section behavior is documented in
  `global-map-removal.md`.
- Landing Dashboard retains metric calculations, health bars, containers, and incident-log replay.
- AIChat retains prompts, generated demo responses, input, copy, play/pause, replay, and launch behavior.
- Pricing retains API-backed plans, billing calculations, monthly/yearly state, availability states, selection, and contact actions.
- Documentation retains copy controls, expandable sections, code samples, API links, GitHub link, and close behavior.
- Blog retains existing article data, links, and mailto subscription action.
- Footer retains contact, LinkedIn, launch, navigation, and social destinations.

## Baseline verification limits

- Source inspection completed.
- Existing frontend tests cover selected landing components (Dashboard, Metrics, Navbar, Pricing) and broader page behavior.
- Browser/device screenshots were NOT VERIFIED because no browser surface was available in the current session.
- No external writes, payments, emails, subscriptions, or deployments were performed.
