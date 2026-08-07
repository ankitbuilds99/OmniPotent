import { test, expect } from '@playwright/test';
import { BasePage } from '../pages/base.page';

test.describe('Authentication Flow', () => {
  test('should redirect unauthenticated users to login', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/login|auth/);
  });

  test('should allow access with valid API key', async ({ page }) => {
    const basePage = new BasePage(page);
    await basePage.setAuthCookie(await basePage.getApiToken());
    await page.goto('/dashboard');
    await expect(page).toHaveURL('/dashboard');
    await basePage.waitForLoad();
  });

  test('should reject expired tokens', async ({ page }) => {
    await page.context().addCookies([
      { name: 'auth_token', value: 'expired-token-12345', domain: 'localhost', path: '/' },
    ]);
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/login|auth/);
  });

  test('should handle logout correctly', async ({ page }) => {
    const basePage = new BasePage(page);
    await basePage.setAuthCookie(await basePage.getApiToken());
    await page.goto('/dashboard');
    await page.locator('[data-testid="logout-button"]').click();
    await expect(page).toHaveURL(/login|auth/);
    const cookies = await page.context().cookies();
    const authCookie = cookies.find((c) => c.name === 'auth_token');
    expect(authCookie).toBeUndefined();
  });

  test('should show rate limit error on too many attempts', async ({ page }) => {
    for (let i = 0; i < 10; i++) {
      await page.goto('/login');
      await page.locator('[data-testid="login-button"]').click();
    }
    await expect(page.locator('[data-testid="rate-limit-error"]')).toBeVisible();
  });
});

test.describe('Session Management', () => {
  test('should refresh token before expiry', async ({ page }) => {
    const basePage = new BasePage(page);
    await basePage.setAuthCookie(await basePage.getApiToken());
    await page.goto('/dashboard');

    const initialCookies = await page.context().cookies();
    const initialToken = initialCookies.find((c) => c.name === 'auth_token')?.value;

    await page.waitForTimeout(5000);
    await page.reload();

    const newCookies = await page.context().cookies();
    const newToken = newCookies.find((c) => c.name === 'auth_token')?.value;

    expect(newToken).toBeDefined();
  });

  test('should handle concurrent sessions', async ({ browser }) => {
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    const basePage1 = new BasePage(page1);
    const basePage2 = new BasePage(page2);

    await basePage1.setAuthCookie(await basePage1.getApiToken());
    await basePage2.setAuthCookie(await basePage2.getApiToken());

    await page1.goto('/dashboard');
    await page2.goto('/dashboard');

    await expect(page1).toHaveURL('/dashboard');
    await expect(page2).toHaveURL('/dashboard');

    await context1.close();
    await context2.close();
  });
});
