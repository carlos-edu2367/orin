import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { BrowserSection } from '../../src/features/settings/BrowserSection'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const playwrightServer = (state = 'active') => ({
  server_id: 'srv-pw',
  slug: 'playwright',
  display_name: 'Playwright',
  transport: 'stdio',
  command: 'npx',
  args: ['-y', '@playwright/mcp@latest'],
  url: null,
  secret_names: [],
  catalog_id: 'playwright',
  state,
  state_reason: '',
  protocol_version: '2024-11-05',
  tool_count: 24,
  auth_kind: 'none',
  tools: [{ name: 'browser_navigate', description: 'Navigate to URL', enabled: true }],
})

describe('BrowserSection', () => {
  it('offers 1-click install of Playwright MCP when not installed and activates it on click', async () => {
    let servers: Array<Record<string, unknown>> = []
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/v1/mcp/servers') && method === 'GET') {
        return Promise.resolve(json(servers))
      }
      if (url.endsWith('/v1/mcp/servers') && method === 'POST') {
        const created = playwrightServer('pending_approval')
        servers = [created]
        return Promise.resolve(json(created, 201))
      }
      if (url.endsWith('/v1/mcp/servers/srv-pw/approve') && method === 'POST') {
        const active = playwrightServer('active')
        servers = [active]
        return Promise.resolve(json(active))
      }
      if (url.endsWith('/v1/mcp/servers/srv-pw/test') && method === 'POST') {
        return Promise.resolve(
          json({ connected: true, protocol_version: '2024-11-05', tools: ['browser_navigate'], error: null }),
        )
      }
      return Promise.resolve(json({}))
    })
    const client = new ApiClient({
      fetchImpl,
      csrfToken: 'test-csrf',
      maxAttempts: 1,
      createIdempotencyKey: () => 'test-idempotency',
    })

    render(<MemoryRouter><BrowserSection client={client} /></MemoryRouter>)
    await userEvent.click(await screen.findByRole('button', { name: 'Instalar e ativar Playwright MCP' }))

    expect(await screen.findByTestId('playwright-mcp-status')).toHaveTextContent('Ativo')
    expect(screen.getByText(/24 ferramentas disponíveis/i)).toBeInTheDocument()
  })

  it('shows active controls (Testar conexão, Desativar, Remover) once Playwright MCP is active', async () => {
    const client = new ApiClient({
      fetchImpl: vi.fn<typeof fetch>(() => Promise.resolve(json([playwrightServer('active')]))),
      csrfToken: 'test-csrf',
      maxAttempts: 1,
    })

    render(<MemoryRouter><BrowserSection client={client} /></MemoryRouter>)

    expect(await screen.findByTestId('playwright-mcp-status')).toHaveTextContent('Ativo')
    expect(screen.getByRole('button', { name: 'Testar conexão' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Desativar' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remover' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Instalar e ativar Playwright MCP' })).not.toBeInTheDocument()
  })

  it('recovers when Playwright MCP is stuck in pending_approval', async () => {
    let approved = false
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/v1/mcp/servers') && method === 'GET') {
        return Promise.resolve(json([playwrightServer(approved ? 'active' : 'pending_approval')]))
      }
      if (url.endsWith('/v1/mcp/servers/srv-pw/approve') && method === 'POST') {
        approved = true
        return Promise.resolve(json(playwrightServer('active')))
      }
      if (url.endsWith('/v1/mcp/servers/srv-pw/test') && method === 'POST') {
        return Promise.resolve(
          json({ connected: true, protocol_version: '2024-11-05', tools: ['browser_navigate'], error: null }),
        )
      }
      return Promise.resolve(json({}))
    })
    const client = new ApiClient({
      fetchImpl,
      csrfToken: 'test-csrf',
      maxAttempts: 1,
      createIdempotencyKey: () => 'test-idempotency',
    })

    render(<MemoryRouter><BrowserSection client={client} /></MemoryRouter>)

    await userEvent.click(await screen.findByRole('button', { name: 'Ativar Playwright MCP' }))
    await waitFor(() => {
      expect(screen.getByTestId('playwright-mcp-status')).toHaveTextContent('Ativo')
    })
  })
})
