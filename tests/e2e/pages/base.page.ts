import { Page, Locator, expect } from '@playwright/test';

export class BasePage {
  readonly page: Page;
  readonly loadingSpinner: Locator;
  readonly errorToast: Locator;
  readonly successToast: Locator;

  constructor(page: Page) {
    this.page = page;
    this.loadingSpinner = page.locator('[data-testid="loading-spinner"]');
    this.errorToast = page.locator('[data-testid="error-toast"]');
    this.successToast = page.locator('[data-testid="success-toast"]');
  }

  async goto(path: string = '/') {
    await this.page.goto(path);
    await this.waitForLoad();
  }

  async waitForLoad() {
    await this.loadingSpinner.waitFor({ state: 'hidden', timeout: 30000 });
  }

  async expectNoErrors() {
    await expect(this.errorToast).not.toBeVisible();
  }

  async setAuthCookie(token: string) {
    await this.page.context().addCookies([
      { name: 'auth_token', value: token, domain: 'localhost', path: '/' },
    ]);
  }

  async getApiToken(): Promise<string> {
    return process.env.TEST_API_TOKEN || 'test-token';
  }
}
