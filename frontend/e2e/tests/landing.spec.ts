import { test, expect } from '@playwright/test';

test.describe('Landing Page', () => {
  test('should display the landing page', async ({ page }) => {
    await page.goto('/');

    // Check for main elements
    await expect(page.locator('text=OmniPotent')).toBeVisible();
  });

  test('should have launch button', async ({ page }) => {
    await page.goto('/');

    // Look for a launch/start button
    const launchButton = page.getByRole('button', { name: /launch|start|get started|try/i });
    await expect(launchButton).toBeVisible();
  });

  test('should navigate to login on launch', async ({ page }) => {
    await page.goto('/');

    const launchButton = page.getByRole('button', { name: /launch|start|get started|try/i });
    await launchButton.click();

    // Should show login options
    await expect(page.locator('text=/sign in|login|continue/i')).toBeVisible({ timeout: 5000 });
  });
});
