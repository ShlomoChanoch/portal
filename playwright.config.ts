import { defineConfig, devices } from '@playwright/test';

/**
 * Portal E2E suite.
 *
 * The suite runs against an already-running dev stack (vite :5173 -> api :8000),
 * because a real deployment needs the runner too. Boot it with `make dev` (or
 * `make dev-podman` for the real OCI pipeline) before running:
 *
 *   npm run test:e2e            # fast UI specs (default)
 *   npm run test:e2e:real       # @real specs: build + run + delete a real Deployment
 *
 * `e2e/global-setup.ts` fails fast with a readable message when the stack is down.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173';

export default defineConfig({
  testDir: './e2e',
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: [['list'], ['html', { open: 'never' }]],
  /* @real specs exercise real builds/runs and are opt-in (see npm scripts). */
  grepInvert: process.env.E2E_REAL ? undefined : /@real/,
  globalSetup: './e2e/global-setup.ts',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    baseURL: BASE_URL,
    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      /* Bundled Chromium — no system Chrome/channel dependency (Arch is unsupported
       * by `npx playwright install chrome`). */
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
