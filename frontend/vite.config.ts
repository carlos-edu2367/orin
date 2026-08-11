import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { env as processEnv } from 'node:process'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_')
  // This is only a UX bootstrap. The API independently enforces loopback
  // access and never trusts this value as an authorization signal.
  const authMode = (env.VITE_AUTH_MODE ?? processEnv.VITE_AUTH_MODE) === 'loopback' ? 'loopback' : ''

  return {
    plugins: [
      react(),
      {
        name: 'agentos-local-auth-mode',
        transformIndexHtml: {
          order: 'pre',
          handler(html: string) {
            return html.replace('name="agentos-auth-mode" content=""', `name="agentos-auth-mode" content="${authMode}"`)
          },
        },
      },
    ],
    server: {
      host: '127.0.0.1',
      port: 4173,
      proxy: { '/v1': 'http://127.0.0.1:8000', '/healthz': 'http://127.0.0.1:8000', '/readyz': 'http://127.0.0.1:8000' },
    },
  }
})
