# OmniPotent landing slide audit

Route: `/marketing`  
Composition: `frontend/src/components/landing/CinematicLanding.tsx`

The public root route is intentionally separate: `/` redirects to `/console`.
The former GlobalMap slide was removed by explicit product approval and is not
part of the current landing composition.

| Slide | Component | Section ID | Existing content / role | Interactive or data-backed | Background treatment |
| --- | --- | --- | --- | --- | --- |
| 01 | `HeroSection` | hero | Existing badge, rotating incident copy, description, CTAs, runtime counts, integrations | Catalog counts from `/config`; launch and demo actions; ambience control | Shared fixed hero video, bright readability layers |
| 02 | `HowItWorks` | — | Five-step investigation tutorial and demo response | Step selection, playback, reset, approval state, launch action | Same shared video and glass surfaces |
| 03 | `Terminal` | — | Deployment walkthrough and terminal output | Replay / progressive output | Same shared video and terminal surface |
| 04 | `FeatureCards` | `features` | Existing OmniPotent capability cards and details | Card hover and expanded details | Same shared video and contextual card accents |
| 05 | `Pipeline` | `pipeline` | Existing investigation-to-resolution stages, logs, and progress | Stage selection, replay, restart | Same shared video and semantic stage colors |
| 06 | landing `Dashboard` | `dashboard` | Existing metrics, health, containers, and incident log | Replay and dashboard visualization state | Same shared video and glass dashboard cards |
| 07 | `AIChat` | — | Existing prompts, responses, and assistant demo | Input, copy, playback, replay, launch action | Same shared video and chat surface |
| 08 | `SecuritySection` | — | Existing security architecture and controls | Informational feature presentation | Same shared video with trust-oriented accents |
| 09 | `Metrics` | — | Existing product metrics and supporting copy | Existing metric animation | Same shared video and neutral glass treatment |
| 10 | `Pricing` | `pricing` | API-backed plans, values, availability, and billing options | Billing toggle, plan selection, contact / launch actions | Same shared video and pricing glass surfaces |
| 11 | `Documentation` | `docs` | Existing API documentation sections and code samples | Copy, expand/collapse, section selection, GitHub link, close | Same shared video and readable code panels |
| 12 | `Blog` | `blog` | Existing guides, links, and subscription action | External guide links and email subscription | Same shared video and readable newsletter glass |
| 13 | `Footer` | footer | Existing CTA, brand, contact, social, and navigation links | Launch, mail, social, and footer navigation actions | Same shared video; legacy 3D scene is not mounted |

## Shared background contract

- `CinematicLanding` mounts exactly one `DecorativeVideo` using the required
  hero media URL.
- The video is fixed behind the complete landing composition and uses
  `object-position: center`.
- Below-fold sections do not mount independent background videos.
- Section backgrounds are transparent over the shared video; only subtle
  contextual accents and glass surfaces vary by section.
- Text, controls, and product visualizations remain above the background.
