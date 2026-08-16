import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { McpFromRepoDialog } from '../../src/features/plugins/McpFromRepoDialog'
import { ApiClient } from '../../src/api/client'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

type FetchInit = Parameters<typeof fetch>[1]
type Route = { method: string; pattern: RegExp; respond: (init?: FetchInit) => Response }

function routedFetch(routes: Route[]) {
  const calls: Array<[string, FetchInit]> = []
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push([url, init])
    const route = routes.find((item) => item.method === method && item.pattern.test(url))
    if (!route) throw new Error(`Unhandled request: ${method} ${url}`)
    return route.respond(init)
  })
  return { fetchImpl, calls }
}

describe('McpFromRepoDialog', () => {
  it('pre-fills the form from a structured inference guess', async () => {
    const { fetchImpl } = routedFetch([
      { method: 'POST', pattern: /infer-mcp$/, respond: () => json({ display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'], url: null, secret_names: ['API_KEY'], confidence: 'structured' }) },
    ])
    render(<McpFromRepoDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} sourceUrl="https://github.com/acme/demo-mcp.git" onClose={vi.fn()} onAdded={vi.fn()} />)

    expect(await screen.findByDisplayValue('demo-mcp')).toBeInTheDocument()
    expect(screen.getByDisplayValue('npx')).toBeInTheDocument()
    expect(screen.getByDisplayValue('-y demo-mcp')).toBeInTheDocument()
    expect(screen.getByDisplayValue('API_KEY')).toBeInTheDocument()
    expect(screen.queryByText(/preencha manualmente/)).not.toBeInTheDocument()
  })

  it('opens a blank form with a note when inference finds nothing', async () => {
    const { fetchImpl } = routedFetch([
      { method: 'POST', pattern: /infer-mcp$/, respond: () => json({ display_name: 'demo-mcp', transport: null, command: null, args: [], url: null, secret_names: [], confidence: 'none' }) },
    ])
    render(<McpFromRepoDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} sourceUrl="https://github.com/acme/demo-mcp.git" onClose={vi.fn()} onAdded={vi.fn()} />)

    expect(await screen.findByText(/preencha manualmente/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('demo-mcp')).toBeInTheDocument()
    expect(screen.getByLabelText('Comando')).toHaveValue('')
  })

  it('submits the (possibly edited) guess and shows the approval card', async () => {
    const { fetchImpl, calls } = routedFetch([
      { method: 'POST', pattern: /infer-mcp$/, respond: () => json({ display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'], url: null, secret_names: [], confidence: 'structured' }) },
      { method: 'POST', pattern: /\/v1\/mcp\/servers$/, respond: () => json({ server_id: 's1', slug: 'demo-mcp', display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'], url: null, secret_names: [], catalog_id: null, state: 'pending_approval', state_reason: '', protocol_version: '', tool_count: 0, tools: [] }, 201) },
    ])
    render(<McpFromRepoDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} sourceUrl="https://github.com/acme/demo-mcp.git" onClose={vi.fn()} onAdded={vi.fn()} />)

    await screen.findByDisplayValue('demo-mcp')
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar' }))

    await waitFor(() => {
      const proposeCall = calls.find(([url, init]) => url.endsWith('/v1/mcp/servers') && init?.method === 'POST')
      expect(proposeCall).toBeTruthy()
      expect(JSON.parse(String(proposeCall?.[1]?.body))).toMatchObject({ display_name: 'demo-mcp', transport: 'stdio', command: 'npx', args: ['-y', 'demo-mcp'] })
    })
    expect(await screen.findByRole('button', { name: 'Conectar' })).toBeInTheDocument()
  })
})
