import { defineConfig } from '@playwright/test'
import { env as processEnv } from 'node:process'

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  // The dev-server scene chunk is shared by the browser specs. Loading the R3F
  // chunk from several workers made otherwise deterministic lazy-scene checks
  // contend for the same Vite process; serialize this small suite instead of
  // inflating assertion timeouts.
  workers: 1,
  // Windows hosted runners can transiently exhaust Chromium's socket buffers
  // while Vite emits harmless proxy failures for mocked API calls. A single CI
  // retry distinguishes that runner condition from a reproducible product bug.
  retries: processEnv.CI ? 1 : 0,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: true,
    // Browser specs inject their own CSRF bootstrap. Do not inherit a developer's
    // loopback-mode environment, which would deliberately bypass that token and
    // make the authentication assertions non-deterministic.
    env: { ...processEnv, VITE_AUTH_MODE: '' },
  },
})
