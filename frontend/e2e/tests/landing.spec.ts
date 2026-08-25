import { test, expect } from '@playwright/test';

// These used to reach for `getByRole('button', { name: /launch|start|get started|try/i })`,
// which matches eleven CTAs on this page and so failed Playwright strict mode
// before it ever asserted anything. Each test now names one element and checks
// a real outcome (a URL change, a specific heading) rather than "something is
// visible".

test.describe('Landing page', () => {
  test('renders the brand and the hero headline', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveTitle(/OmniPotent/);
    await expect(page.getByTestId('nav-brand')).toBeVisible();

    // The hero is the only <h1> on the landing route.
    const headline = page.getByRole('heading', { level: 1 });
    await expect(headline).toHaveCount(1);
    await expect(headline).toContainText('AI agents that');
  });

  test('the nav CTA routes to /login', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('nav-launch-console').click();

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByText('Welcome back', { exact: true })).toBeVisible();
  });

  test('the anchor links target sections that exist', async ({ page }) => {
    await page.goto('/');

    for (const id of ['features', 'pipeline', 'pricing']) {
      await expect(page.locator(`#${id}`)).toHaveCount(1);
    }
  });

  test('an unknown path falls back to the landing route', async ({ page }) => {
    // The backend serves index.html for any unrecognised non-API path and
    // App.tsx redirects the wildcard route home; both halves have to hold or a
    // shared link 404s.
    await page.goto('/definitely-not-a-route');

    await expect(page).toHaveURL(/localhost:\d+\/$/);
    await expect(page.getByTestId('nav-launch-console')).toBeVisible();
  });
});
