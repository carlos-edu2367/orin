import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useRoutes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ThemeProvider } from '../../src/app/ThemeContext'
import { routes } from '../../src/app/routes'
import { McpSection } from '../../src/features/mcp/McpSection'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function createServersClient(servers: Array<Record<string, unknown>> = []): ApiClient {
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (method === 'GET' && /\/v1\/mcp\/servers$/.test(url)) {
      return json(servers)
    }
    throw new Error(`Unhandled request: ${method} ${url}`)
  })
  return new ApiClient({
    fetchImpl,
    csrfToken: 'csrf-test',
    maxAttempts: 1,
    createIdempotencyKey: () => 'intent-test',
  })
}

function RoutedApp() {
  return useRoutes(routes)
}

describe('Connections (McpSection) — Phase 6 (ref-13.png)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders header, subtitle, + Adicionar conexão CTA, mode tabs, honest built-in capabilities, and MCP server cards', async () => {
    const client = createServersClient([
      {
        server_id: 'srv-gh',
        slug: 'github',
        display_name: 'GitHub',
        transport: 'stdio',
        command: 'npx',
        args: ['-y', 'server-github'],
        url: null,
        secret_names: [],
        catalog_id: 'github',
        state: 'active',
        state_reason: '',
        protocol_version: '2025-06-18',
        tool_count: 2,
      },
    ])

    render(
      <MemoryRouter initialEntries={['/connections']}>
        <McpSection client={client} />
      </MemoryRouter>,
    )

    const heading = await screen.findByTestId('connections-heading')
    expect(heading).toHaveTextContent('Conexões')
    expect(
      screen.getByText('Serviços e pastas que o Orin pode acessar quando você pedir.'),
    ).toBeInTheDocument()

    const addBtn = screen.getByTestId('connections-add-button')
    expect(addBtn).toHaveTextContent('+ Adicionar conexão')

    const tablist = screen.getByRole('tablist', { name: /Modo de conexões/i })
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Apps conectados',
      'Permissões',
      'Avançado (MCP)',
    ])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(tablist.contains(addBtn)).toBe(false)

    const list = screen.getByTestId('connections-list')
    expect(within(list).queryByTestId('connection-card-conn-auryly')).not.toBeInTheDocument()
    expect(screen.queryByText(/Auryly/i)).not.toBeInTheDocument()

    const browserCard = within(list).getByTestId('connection-card-conn-browser')
    expect(within(browserCard).getByText('Navegador')).toBeInTheDocument()
    expect(
      within(browserCard).getByText('Pesquisa sites e captura páginas com sua permissão'),
    ).toBeInTheDocument()
    expect(within(browserCard).getByText('Ativo')).toBeInTheDocument()

    const filesCard = within(list).getByTestId('connection-card-conn-files')
    expect(within(filesCard).getByText('Arquivos locais')).toBeInTheDocument()
    expect(within(filesCard).getByText('Restrito')).toBeInTheDocument()

    // Also renders MCP server cards returned by listMcpServers
    const ghCard = await screen.findByRole('article', { name: /GitHub/ })
    expect(within(ghCard).getByText(/2 tools/)).toBeInTheDocument()
  })

  it('renders the right-hand inspector with Navegador selected by default, honest capability permissions, and disconnect button', async () => {
    const user = userEvent.setup()
    const client = createServersClient([])

    render(
      <MemoryRouter initialEntries={['/connections']}>
        <McpSection client={client} />
      </MemoryRouter>,
    )

    const inspector = await screen.findByTestId('connections-inspector-panel')
    const inspectorScope = within(inspector)

    expect(inspectorScope.getByRole('heading', { name: 'Navegador' })).toBeInTheDocument()
    expect(inspectorScope.getByText('Ativo')).toBeInTheDocument()

    const useInSelect = inspectorScope.getByLabelText('Usar em') as HTMLSelectElement
    expect(Array.from(useInSelect.options).map((o) => o.textContent)).toEqual([
      'Todos os projetos',
      'Apenas no projeto atual',
    ])
    expect(useInSelect.value).toBe('Todos os projetos')

    await user.selectOptions(useInSelect, 'Apenas no projeto atual')
    expect(useInSelect.value).toBe('Apenas no projeto atual')

    expect(inspectorScope.getByText('O que o Orin pode fazer')).toBeInTheDocument()

    const navigatePerm = inspectorScope.getByRole('checkbox', {
      name: /Navegar em páginas públicas quando solicitado/i,
    })
    const capturePerm = inspectorScope.getByRole('checkbox', {
      name: /Capturar leitura de página com confirmação/i,
    })

    expect(navigatePerm).toBeChecked()
    expect(capturePerm).toBeChecked()

    // Toggling a checkbox updates checked state immediately
    await user.click(capturePerm)
    expect(capturePerm).not.toBeChecked()

    expect(
      inspectorScope.getByText('Toda ação importante pede sua confirmação no chat.'),
    ).toBeInTheDocument()

    const disconnectBtn = inspectorScope.getByTestId('connections-disconnect-button')
    expect(disconnectBtn).toHaveTextContent('Desconectar Navegador')

    // Selecting Arquivos locais updates the inspector with file permissions
    const list = screen.getByTestId('connections-list')
    await user.click(within(list).getByText('Arquivos locais'))
    expect(inspectorScope.getByRole('heading', { name: 'Arquivos locais' })).toBeInTheDocument()
    expect(
      inspectorScope.getByRole('checkbox', { name: /Ler arquivos nas pastas autorizadas/i }),
    ).toBeChecked()
    expect(
      inspectorScope.getByRole('checkbox', { name: /Pedir confirmação antes de editar arquivos/i }),
    ).toBeChecked()
    expect(inspectorScope.getByTestId('connections-disconnect-button')).toHaveTextContent(
      'Desconectar Arquivos locais',
    )
  })

  it('registers direct routes /memory and /connections in routes.tsx', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        if (url.includes('/v1/mcp/servers') || url.includes('/v1/plugins')) {
          return json([])
        }
        return json({ items: [] })
      }),
    )

    const { unmount } = render(
      <MemoryRouter initialEntries={['/memory']}>
        <ThemeProvider>
          <RoutedApp />
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('memory-heading')).toHaveTextContent('Memória sob seu controle')
    unmount()

    render(
      <MemoryRouter initialEntries={['/connections']}>
        <ThemeProvider>
          <RoutedApp />
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('connections-heading')).toHaveTextContent('Conexões')
  })
})
