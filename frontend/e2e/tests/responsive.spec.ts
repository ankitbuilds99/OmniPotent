import { test, expect } from '@playwright/test';

test.describe('Responsive Design', () => {
  test('mobile viewport shows content', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 }); // iPhone SE
    await page.goto('/');

    // Page should load without horizontal scroll
    const body = page.locator('body');
    await expect(body).toBeVisible();

    // Check no horizontal overflow
    const scrollWidth = await page.evaluate(() => document.body.scrollWidth);
    const clientWidth = await page.evaluate(() => document.body.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 10); // Allow small margin
  });

  test('tablet viewport shows content', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 }); // iPad
    await page.goto('/');

    const body = page.locator('body');
    await expect(body).toBeVisible();
  });

  test('desktop viewport shows full layout', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/');

    const body = page.locator('body');
    await expect(body).toBeVisible();
  });

  test('navigation adapts to mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');

    // Navigation should be visible (possibly as hamburger menu)
    const nav = page.locator('header, nav, .nav, [role="navigation"]');
    if (await nav.count() > 0) {
      await expect(nav.first()).toBeVisible();
    }
  });
});
