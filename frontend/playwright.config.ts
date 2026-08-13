import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  // The dev-server scene chunk is shared by the browser specs. Loading the R3F
  // chunk from several workers made otherwise deterministic lazy-scene checks
  // contend for the same Vite process; serialize this small suite instead of
  // inflating assertion timeouts.
  workers: 1,
  retries: 0,
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
    env: { ...process.env, VITE_AUTH_MODE: '' },
  },
})
