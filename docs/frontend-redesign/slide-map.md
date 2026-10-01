# OmniPotent landing slide map

Active route: `/marketing`  
Composition: `frontend/src/components/landing/CinematicLanding.tsx`

| # | Component | ID | Existing heading / role | Interactive? | Data source | Context | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | `HeroSection` | hero | `AI agents that resolve ...` | Yes | `/config`, local theme | product / AI | Badge, rotating copy, CTAs, stats, integrations |
| 02 | `HowItWorks` | — | `From question to resolution` | Yes | Local tutorial state | AI / success | Five-step demo, controls, launch action |
| 03 | `Terminal` | — | Deployment walkthrough | Yes | Local terminal demo state | product / evidence | Terminal output and replay |
| 04 | `FeatureCards` | `features` | `Everything you need to automate DevOps` | Yes | Local feature data | product | Existing capability cards and details |
| 05 | `Pipeline` | `pipeline` | Investigation pipeline | Yes | Local pipeline data | evidence / AI | Stages, logs, progress, restart |
| 06 | landing `Dashboard` | `dashboard` | Operations dashboard | Yes | Local calculated demo data | evidence / success | Metrics, health, containers, incident replay |
| 07 | `AIChat` | — | Assistant demonstration | Yes | Local demo responses | AI | Prompts, responses, copy, playback |
| 08 | `SecuritySection` | — | Enterprise security | No | Static architectural copy | trust | Security feature presentation |
| 09 | `Metrics` | — | Product metrics | Yes | Existing metric data | neutral / evidence | Existing metric animation |
| 10 | `Pricing` | `pricing` | Pricing and plans | Yes | API-backed plans | product | Billing toggle, plan selection, contact actions |
| 11 | `Documentation` | `docs` | API documentation | Yes | Existing documentation data | evidence | Copy, sections, code, GitHub link |
| 12 | `Blog` | `blog` | Docs and guides | Yes | Existing guide data | neutral | External guide links and email subscription |
| 13 | `Footer` | footer | Final CTA and site links | Yes | Static link data | trust / product | Launch, contact, social, navigation |

The checkout currently contains 13 major landing components after the approved
removal of `GlobalMap`; it is not forced into an artificial count of 12.

## Shared environment

`CinematicLanding` mounts one persistent `DecorativeVideo` using the required
reference URL from `landingMedia.ts`. The video is fixed behind the landing
content, uses centered cover positioning, and is controlled by the existing
ambience pause action. No other landing slide mounts a full-page background
video or separate Three.js environment.
