import { test, expect } from '@playwright/test';

test.describe('API Health Checks', () => {
  test('should return healthy status from /healthz', async ({ request }) => {
    const response = await request.get('/healthz');
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.status).toBe('ok');
  });

  test('should return OpenAPI docs from /docs', async ({ request }) => {
    const response = await request.get('/docs');
    expect(response.ok()).toBeTruthy();
  });
});

test.describe('API Error Handling', () => {
  test('should return 404 for unknown routes', async ({ request }) => {
    const response = await request.get('/api/v1/nonexistent-route-12345');
    expect(response.status()).toBe(404);
  });
});
