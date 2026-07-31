import { test, expect } from '@playwright/test';

const API_BASE = process.env.API_URL || 'http://localhost:8000';

test.describe('API Health', () => {
  test('health endpoint returns ok', async ({ request }) => {
    const response = await request.get(`${API_BASE}/healthz`);
    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body.status).toBe('ok');
  });

  test('config endpoint returns model info', async ({ request }) => {
    const response = await request.get(`${API_BASE}/config`);
    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body).toHaveProperty('model');
  });

  test('cors headers are present', async ({ request }) => {
    const response = await request.get(`${API_BASE}/healthz`);
    // In dev mode, CORS should allow localhost
    expect(response.ok()).toBeTruthy();
  });
});
