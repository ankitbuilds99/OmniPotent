import { defineConfig, devices } from '@playwright/test';

// Browser-level E2E for the SPA. The sibling suite at tests/e2e drives the
// FastAPI origin directly; this one drives the Vite dev server so the React
// routes, not just the JSON API, are covered.
//
// Two things used to make this suite impossible to run:
//   * webServer ran `npm run dev` with cwd = this directory, and e2e/package.json
//     has no `dev` script — so every invocation died with `Missing script: "dev"`.
//     `cwd: '..'` (resolved against this config's directory by Playwright) points
//     it at frontend/package.json, which does.
//   * the URLs said :3000 while frontend/vite.config.ts pins `port: 5173`.
// Both are now derived from one constant so they cannot drift apart again.

const PORT = Number(process.env.FRONTEND_PORT || 5173);
const BASE_URL = process.env.BASE_URL || `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  // `open: 'never'` — the default html reporter launches a browser at the end of
  // a failing local run, which hangs a non-interactive shell.
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'Mobile Safari',
      use: { ...devices['iPhone 12'] },
    },
  ],
  // Skipped when BASE_URL is set, i.e. when something else is already serving
  // the SPA (the FastAPI single-origin deployment, a preview build, a tunnel).
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: `npm run dev -- --port ${PORT} --strictPort`,
        cwd: '..',
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120 * 1000,
      },
});
