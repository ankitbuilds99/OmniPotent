import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/e2e/**',
    ],
    coverage: {
      enabled: true,
      provider: 'v8',
      reporter: ['text', 'json', 'html', 'lcov'],
      reportsDirectory: './coverage',
      exclude: [
        'node_modules/',
        'dist/',
        'e2e/',
        '**/*.d.ts',
        '**/*.config.{js,ts}',
        '**/test/**',
        '**/__mocks__/**',
        '**/types/**',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
      // A floor to ratchet upward, not a target. These were 70/70/60/70
      // against an actual 0.99% line coverage — two test files for twenty
      // page components — so the step failed on every run and was kept off
      // the critical path with continue-on-error. That silenced real test
      // failures too, which is the part that mattered.
      //
      // Set just under what the suite measures today, so the step passes and
      // a genuine failure fails the build. Raise these as tests get written;
      // the numbers are meant to be embarrassing enough to prompt that.
      //
      // Ratcheted once already: 0.9/10/40/0.9 -> here, after page-level tests
      // for OnCall, Postmortem, RunbookBuilder, ServiceMap, Team,
      // IntegrationMarketplace, OnboardingWizard and the api client took line
      // coverage from 0.99% to 20.8%.
      thresholds: {
        lines: 20,
        functions: 38,
        branches: 72,
        statements: 20,
      },
    },
  },
});
