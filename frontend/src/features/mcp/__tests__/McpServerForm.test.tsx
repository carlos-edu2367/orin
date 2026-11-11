import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import { ApiError } from '../../../api/errors'
import * as mcpApi from '../../../api/mcp'
import { McpApprovalCard } from '../../conversations/McpApprovalCard'
import { McpServerForm } from '../McpServerForm'

vi.mock('../../../app/useSession', () => ({
  useSession: () => ({
    mode: 'local',
    user: null,
    capabilities: {
      mcp_stdio: true,
      local_workspaces: true,
      playwright_browser: true,
      desktop_shell: false,
    },
    isAdmin: false,
    refresh: vi.fn(),
    signOut: vi.fn(),
  }),
}))

vi.mock('../../../api/mcp', async () => {
  const actual = await vi.importActual<typeof import('../../../api/mcp')>('../../../api/mcp')
  return {
    ...actual,
    listMcpCatalog: vi.fn(),
    createMcpServer: vi.fn(),
    approveMcpServer: vi.fn(),
  }
})

const mockClient = {
  request: vi.fn(),
  createMutationIntent: vi.fn(() => ({ idempotencyKey: 'test-key' })),
} as unknown as ApiClient

describe('McpServerForm & McpApprovalCard custom HTTP headers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(mcpApi.listMcpCatalog).mockResolvedValue([])
    vi.mocked(mcpApi.createMcpServer).mockResolvedValue({
      server_id: 'srv_clickup',
      slug: 'clickup',
      display_name: 'clickup',
      transport: 'http',
      command: null,
      args: [],
      url: 'https://mcp.team.contraestatisticallc.com/mcp',
      secret_names: ['header:Authorization'],
      catalog_id: null,
      state: 'pending_approval',
      state_reason: '',
      protocol_version: '',
      tool_count: 0,
      auth_kind: 'static',
      tools: [],
    })
    vi.mocked(mcpApi.approveMcpServer).mockResolvedValue({
      server_id: 'srv_clickup',
      slug: 'clickup',
      display_name: 'clickup',
      transport: 'http',
      command: null,
      args: [],
      url: 'https://mcp.team.contraestatisticallc.com/mcp',
      secret_names: ['header:Authorization'],
      catalog_id: null,
      state: 'active',
      state_reason: '',
      protocol_version: '2025-03-26',
      tool_count: 2,
      auth_kind: 'static',
      tools: [],
    })
  })

  it('imports the clickup command, creates the server with header:Authorization, and approves with the header value', async () => {
    const onCreated = vi.fn()
    const onClose = vi.fn()

    render(<McpServerForm client={mockClient} onCreated={onCreated} onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: 'Configurar manualmente' }))

    const commandTextarea = screen.getByLabelText(/Colar comando/i)
    fireEvent.change(commandTextarea, {
      target: {
        value:
          'claude mcp add --transport http clickup https://mcp.team.contraestatisticallc.com/mcp --header "Authorization: Bearer <chave>" --scope user',
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Importar' }))

    expect(screen.getByLabelText('Nome')).toHaveValue('clickup')
    expect(screen.getByLabelText('Transporte')).toHaveValue('http')
    expect(screen.getByLabelText('URL')).toHaveValue(
      'https://mcp.team.contraestatisticallc.com/mcp',
    )
    expect(screen.getByLabelText('Nome do cabeçalho')).toHaveValue('Authorization')
    expect(screen.getByLabelText('Valor do cabeçalho')).toHaveValue('Bearer <chave>')

    fireEvent.click(screen.getByRole('button', { name: 'Adicionar' }))

    await waitFor(() => {
      expect(mcpApi.createMcpServer).toHaveBeenCalledWith(mockClient, {
        display_name: 'clickup',
        transport: 'http',
        command: undefined,
        args: undefined,
        url: 'https://mcp.team.contraestatisticallc.com/mcp',
        secret_names: ['header:Authorization'],
      })
    })

    await waitFor(() => {
      expect(mcpApi.approveMcpServer).toHaveBeenCalledWith(mockClient, 'srv_clickup', {
        'header:Authorization': 'Bearer <chave>',
      })
    })

    expect(onCreated).toHaveBeenCalledTimes(1)
  })

  it('rejects a reserved header name with a pt-BR error and does not submit', async () => {
    const onCreated = vi.fn()
    const onClose = vi.fn()

    render(<McpServerForm client={mockClient} onCreated={onCreated} onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: 'Configurar manualmente' }))

    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'meu-mcp' } })
    fireEvent.change(screen.getByLabelText('Transporte'), { target: { value: 'http' } })
    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://example.com/mcp' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Adicionar cabeçalho' }))
    fireEvent.change(screen.getByLabelText('Nome do cabeçalho'), {
      target: { value: 'Host' },
    })
    fireEvent.change(screen.getByLabelText('Valor do cabeçalho'), {
      target: { value: 'evil.example.com' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Adicionar' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/reservado/i)
    expect(mcpApi.createMcpServer).not.toHaveBeenCalled()
    expect(mcpApi.approveMcpServer).not.toHaveBeenCalled()
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('surfaces OAuth vs rejected credential errors when approveMcpServer fails and keeps the form open', async () => {
    const onCreated = vi.fn()
    const onClose = vi.fn()

    vi.mocked(mcpApi.approveMcpServer).mockRejectedValueOnce(
      new ApiError({
        status: 401,
        code: 'mcp_authorization_required',
        messageKey: 'mcp_authorization_required',
        retryable: false,
      }),
    )

    render(<McpServerForm client={mockClient} onCreated={onCreated} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Configurar manualmente' }))

    fireEvent.change(screen.getByLabelText(/Colar comando/i), {
      target: {
        value:
          'claude mcp add --transport http clickup https://mcp.team.contraestatisticallc.com/mcp --header "Authorization: Bearer <chave>"',
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Importar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/o servidor pediu login \(OAuth\)/i)
    expect(onCreated).toHaveBeenCalledTimes(1)
    expect(onCreated).toHaveBeenCalledWith({ keepOpen: true })
  })

  it('renders Cabeçalho Authorization label with hint in McpApprovalCard and passes raw header:Authorization to onApprove', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined)
    const onDecline = vi.fn().mockResolvedValue(undefined)

    render(
      <McpApprovalCard
        server={{
          server_id: 'srv_clickup',
          display_name: 'ClickUp MCP',
          transport: 'http',
          secret_names: ['header:Authorization'],
          catalog_id: null,
        }}
        active
        onApprove={onApprove}
        onDecline={onDecline}
      />,
    )

    expect(screen.getByText('Cabeçalho Authorization')).toBeInTheDocument()
    expect(
      screen.getByText('Cole o valor completo, ex.: Bearer sua-chave'),
    ).toBeInTheDocument()

    const input = screen.getByLabelText('Cabeçalho Authorization')
    fireEvent.change(input, { target: { value: 'Bearer pk_12345' } })
    fireEvent.click(screen.getByRole('button', { name: 'Conectar' }))

    await waitFor(() => {
      expect(onApprove).toHaveBeenCalledWith({
        'header:Authorization': 'Bearer pk_12345',
      })
    })
  })
})
