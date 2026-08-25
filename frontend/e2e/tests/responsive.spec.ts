import { test, expect } from '@playwright/test';

// `expect(body).toBeVisible()` passes on a blank page, and the mobile-nav check
// was wrapped in `if (count > 0)` so it asserted nothing when the nav was
// missing — which is the only case worth catching. Each viewport now has to
// render the hero and fit horizontally.

const VIEWPORTS = [
  { name: 'mobile', width: 375, height: 667 }, // iPhone SE
  { name: 'tablet', width: 768, height: 1024 }, // iPad
  { name: 'desktop', width: 1920, height: 1080 },
];

test.describe('Responsive design', () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name} (${vp.width}px) renders the hero without horizontal overflow`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto('/');

      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.locator('nav').first()).toBeVisible();

      const { scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      // A few px of slack for sub-pixel layout rounding; a real overflow is
      // hundreds of px wide.
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 10);
    });
  }

  test('the primary CTA stays reachable on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');

    const cta = page.getByTestId('nav-launch-console');
    await expect(cta).toBeVisible();
    await cta.click();
    await expect(page).toHaveURL(/\/login$/);
  });
});
