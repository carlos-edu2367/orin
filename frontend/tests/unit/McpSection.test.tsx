import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { McpSection } from '../../src/features/mcp/McpSection'

function client(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function summary(overrides: Record<string, unknown> = {}) {
  return {
    server_id: 's1', slug: 'github', display_name: 'GitHub', transport: 'stdio', command: 'npx',
    args: ['-y', 'server-github'], url: null, secret_names: ['GITHUB_PERSONAL_ACCESS_TOKEN'], catalog_id: 'github',
    state: 'active', state_reason: '', protocol_version: '2025-06-18', tool_count: 1, ...overrides,
  }
}

function detail(overrides: Record<string, unknown> = {}) {
  return { ...summary(overrides), tools: [{ name: 'search', description: 'Search repositories', enabled: true }] }
}

function catalogEntry() {
  return {
    catalog_id: 'notion', display_name: 'Notion', summary: 'Páginas e bancos de dados do seu workspace.',
    transport: 'http', setup_instructions: 'Autoriza na primeira conexão.', arguments: [],
    secrets: [],
  }
}

type FetchInit = Parameters<typeof fetch>[1]
type Route = { method: string; pattern: RegExp; respond: (url: string, init?: FetchInit) => Response }

function routedFetch(routes: Route[]) {
  const calls: Array<[string, FetchInit]> = []
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push([url, init])
    const route = routes.find((item) => item.method === method && item.pattern.test(url))
    if (!route) throw new Error(`Unhandled request: ${method} ${url}`)
    return route.respond(url, init)
  })
  return { fetchImpl, calls }
}

function renderSection(routes: Route[]) {
  const { fetchImpl, calls } = routedFetch(routes)
  render(
    <MemoryRouter>
      <McpSection client={client(fetchImpl)} />
    </MemoryRouter>,
  )
  return { calls }
}

describe('McpSection', () => {
  it('lists the configured servers with their state and tool count', async () => {
    renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([summary()]) },
    ])

    expect(await screen.findByText('GitHub')).toBeInTheDocument()
    const card = screen.getByRole('article', { name: /GitHub/ })
    expect(within(card).getByText(/1 tool/)).toBeInTheDocument()
    expect(within(card).getByText('Ativo')).toBeInTheDocument()
  })

  it('shows the approval form and a completion action for a pending server', async () => {
    renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([summary({ state: 'pending_approval', tool_count: 0 })]) },
    ])

    const card = await screen.findByRole('article', { name: /GitHub/ })
    expect(within(card).getByText(/Aguardando aprovação/)).toBeInTheDocument()
    expect(within(card).getByLabelText('GITHUB_PERSONAL_ACCESS_TOKEN')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Conectar' })).toBeInTheDocument()
  })

  it('opens the curated catalog with search when adding a server', async () => {
    const user = userEvent.setup()
    renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([]) },
      { method: 'GET', pattern: /\/v1\/mcp\/catalog/, respond: () => json({ entries: [catalogEntry()] }) },
    ])

    await user.click(await screen.findByRole('button', { name: 'Adicionar servidor' }))

    expect(await screen.findByText('Notion')).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: /catálogo/i })).toBeInTheDocument()
  })

  it('proposes a server chosen from the catalog', async () => {
    const user = userEvent.setup()
    const { calls } = renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([]) },
      { method: 'GET', pattern: /\/v1\/mcp\/catalog/, respond: () => json({ entries: [catalogEntry()] }) },
      { method: 'POST', pattern: /\/v1\/mcp\/servers$/, respond: () => json(detail({ display_name: 'Notion', state: 'pending_approval', tool_count: 0 }), 201) },
    ])

    await user.click(await screen.findByRole('button', { name: 'Adicionar servidor' }))
    await user.click(await screen.findByText('Notion'))
    await user.click(screen.getByRole('button', { name: /Conectar Notion|Adicionar Notion/ }))

    await waitFor(() => {
      const proposeCall = calls.find(([url, init]) => url.endsWith('/v1/mcp/servers') && init?.method === 'POST')
      expect(proposeCall).toBeTruthy()
      expect(JSON.parse(String(proposeCall?.[1]?.body))).toMatchObject({ catalog_id: 'notion', display_name: 'Notion' })
    })
  })

  it('adds a manual server with an explicit transport and secret names', async () => {
    const user = userEvent.setup()
    const { calls } = renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([]) },
      { method: 'GET', pattern: /\/v1\/mcp\/catalog/, respond: () => json({ entries: [] }) },
      { method: 'POST', pattern: /\/v1\/mcp\/servers$/, respond: () => json(detail({ display_name: 'Meu Servidor', state: 'pending_approval', tool_count: 0, secret_names: ['API_TOKEN'] }), 201) },
    ])

    await user.click(await screen.findByRole('button', { name: 'Adicionar servidor' }))
    await user.click(await screen.findByRole('button', { name: 'Configurar manualmente' }))
    await user.type(screen.getByLabelText('Nome'), 'Meu Servidor')
    await user.selectOptions(screen.getByLabelText('Transporte'), 'http')
    await user.type(screen.getByLabelText('URL'), 'https://mcp.example.com/v1')
    await user.type(screen.getByLabelText('Credenciais necessárias'), 'API_TOKEN')
    await user.click(screen.getByRole('button', { name: 'Adicionar' }))

    await waitFor(() => {
      const proposeCall = calls.find(([url, init]) => url.endsWith('/v1/mcp/servers') && init?.method === 'POST')
      expect(proposeCall).toBeTruthy()
      expect(JSON.parse(String(proposeCall?.[1]?.body))).toMatchObject({
        display_name: 'Meu Servidor', transport: 'http', url: 'https://mcp.example.com/v1', secret_names: ['API_TOKEN'],
      })
    })
  })

  it('toggles an individual tool and reflects the new state', async () => {
    const user = userEvent.setup()
    const { calls } = renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([summary()]) },
      { method: 'GET', pattern: /\/v1\/mcp\/servers\/s1$/, respond: () => json(detail()) },
      { method: 'PUT', pattern: /\/tools\/search\/enabled$/, respond: () => json(detail({ tool_count: 1 } as Record<string, unknown>)) },
    ])

    const card = await screen.findByRole('article', { name: /GitHub/ })
    await user.click(within(card).getByRole('button', { name: /Ver tools/ }))
    const toolToggle = await within(card).findByRole('checkbox', { name: 'search' })
    await user.click(toolToggle)

    await waitFor(() => {
      const toggleCall = calls.find(([url]) => url.includes('/tools/search/enabled'))
      expect(toggleCall).toBeTruthy()
      expect(JSON.parse(String(toggleCall?.[1]?.body))).toEqual({ enabled: false })
    })
  })

  it('asks for confirmation before removing a server', async () => {
    const user = userEvent.setup()
    const { calls } = renderSection([
      { method: 'GET', pattern: /\/v1\/mcp\/servers$/, respond: () => json([summary()]) },
      { method: 'DELETE', pattern: /\/v1\/mcp\/servers\/s1$/, respond: () => new Response(null, { status: 204 }) },
    ])

    const card = await screen.findByRole('article', { name: /GitHub/ })
    await user.click(within(card).getByRole('button', { name: 'Remover' }))
    expect(calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)

    await user.click(within(card).getByRole('button', { name: 'Confirmar remoção' }))

    await waitFor(() => expect(calls.some(([, init]) => init?.method === 'DELETE')).toBe(true))
  })
})
