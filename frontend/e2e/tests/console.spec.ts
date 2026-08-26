import { test, expect } from '@playwright/test';

// The previous version of this file clicked a nine-way-ambiguous "launch"
// locator in beforeEach (strict-mode violation, so every test errored before
// its body ran) and then asserted `expect(count).toBeGreaterThanOrEqual(0)` —
// a tautology that passes on a blank page. /console is a plain route in
// App.tsx, so we can just navigate to it and assert what it actually renders.

const SUGGESTIONS = [
  'Why is the checkout API throwing 500 errors?',
  'What deploy caused the checkout failures?',
  'Are any pods crashing in Kubernetes?',
  'Have we seen this incident before?',
];

test.describe('Console', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/console');
  });

  test('renders the empty state with its suggestion prompts', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: 'What can I help investigate?' })
    ).toBeVisible();

    for (const text of SUGGESTIONS) {
      await expect(page.getByRole('button', { name: text })).toBeVisible();
    }
  });

  test('the composer accepts input and enables Send', async ({ page }) => {
    const composer = page.getByRole('textbox', { name: 'Ask about an incident' });
    await expect(composer).toBeVisible();

    const send = page.getByRole('button', { name: 'Send message' });
    // Empty composer: Send stays disabled, so an empty investigation cannot
    // be submitted.
    await expect(send).toBeDisabled();

    await composer.fill('why is checkout 500ing?');
    await expect(composer).toHaveValue('why is checkout 500ing?');
    await expect(send).toBeEnabled();
  });

  test('the top bar links back to the landing page', async ({ page }) => {
    const nav = page.locator('header.nav');
    await expect(nav).toBeVisible();

    await nav.getByRole('button', { name: 'OmniPotent' }).click();
    await expect(page).toHaveURL(/localhost:\d+\/$/);
  });
});
