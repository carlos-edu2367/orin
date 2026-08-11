import { defineConfig } from '@playwright/test'

// A second, deliberately separate Playwright config (Fase 6, IMPLEMENTATION_PLAN.md
// "Decisões locais registradas para a Fase 6"): visual regression baselines are a
// different kind of artifact than the functional `tests/e2e` suite (binary images
// checked into the repo, compared pixel-by-pixel, expected to be updated
// deliberately with `--update-snapshots` rather than fixed like a failing
// assertion). Playwright has no built-in way to run a second `testDir` under the
// same config without either mixing it into the default `npm run test:e2e` run or
// introducing project filtering complexity, so a second config file — mirroring
// `playwright.config.ts` except for `testDir` — is the smallest change that keeps
// `npm run test:e2e` exercising only `tests/e2e` by default while still giving
// visual regression its own `npm run test:visual` entry point.
export default defineConfig({
  testDir: './tests/visual',
  fullyParallel: false,
  retries: 0,
  expect: {
    // Text rendering differs by a handful of subpixels between runs on the same
    // machine. A tiny tolerance keeps the baselines meaningful instead of
    // failing on antialiasing and training everyone to re-record them.
    toHaveScreenshot: { maxDiffPixels: 40 },
  },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: true,
  },
})
