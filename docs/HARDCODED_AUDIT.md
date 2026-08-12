# Hardcoded Value Audit

This repo has a lot of demo and showcase behavior by design. The goal of the recent cleanup was not to remove that personality, but to move repeated environment assumptions into shared runtime/config code.

## What Was Centralized

- Backend repo and logs sample paths now live in `app/runtime_defaults.py`.
- Backend/frontend URL defaults now live in `app/runtime_defaults.py`.
- Frontend API base URL resolution now goes through `frontend/src/lib/runtime.ts`.
- Frontend API clients now use runtime URL resolution instead of repeating localhost literals.

## What Is Still Intentionally Demo-Backed

- The bundled `sample_repo` remains the default repo fixture.
- The `checkout-svc` incident scenario remains the canonical offline demo.
- Theme choices are still static in `frontend/src/themes.ts` because they are part of the UI design system, not environment config.

## Remaining Hardcoded Areas To Review Later

- Demo incident names and fixture payloads in `app/mcp/fixtures/`.
- Demo copy in docs and runbooks that references `checkout-svc`.
- Hardcoded GitHub/org examples in docs and onboarding text.
- Billing and Stripe fallback defaults in backend services.

## Notes

- These remaining values are not accidental leaks. They are mostly showcase/demo fixtures and can stay if the goal is to preserve the current product story.
- If you want a stricter production posture, the next step would be to split demo fixtures into a clearly isolated demo package and make production imports explicit.

