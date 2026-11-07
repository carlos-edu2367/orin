import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    css: true,
    testTimeout: 15_000,
    exclude: ['./tests/e2e/**', './tests/visual/**', '**/node_modules/**', '**/dist/**'],
  },
})
