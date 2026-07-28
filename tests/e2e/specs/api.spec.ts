import { test, expect } from '@playwright/test';

test.describe('API Health Checks', () => {
  test('should return healthy status from /healthz', async ({ request }) => {
    const response = await request.get('/healthz');
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.status).toBe('healthy');
  });

  test('should return API version from /api/v1/version', async ({ request }) => {
    const response = await request.get('/api/v1/version');
    expect(response.ok()).toBeTruthy();
  });
});

test.describe('API Error Handling', () => {
  test('should return 404 for unknown routes', async ({ request }) => {
    const response = await request.get('/api/v1/nonexistent');
    expect(response.status()).toBe(404);
  });

  test('should require authentication for protected endpoints', async ({ request }) => {
    const response = await request.get('/api/v1/investigations');
    expect([401, 403]).toContain(response.status());
  });
});
