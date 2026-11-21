import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { CustomAgentsSection } from '../../agents/CustomAgentsSection'
import { AgentsSection } from '../../codeSettings/AgentsSection'
import { OrchestrationSection } from '../OrchestrationSection'

type RawSettingItem = {
  path: string
  type: 'boolean' | 'number' | 'string' | 'choice' | 'string_list'
  group: string
  label: string
  description: string
  scope: 'user_only' | 'project_off_only' | 'any'
  default: unknown
  value: unknown
  layer: 'default' | 'user' | 'project' | 'local' | 'cli' | 'managed'
  user_value: unknown
  has_user_override: boolean
  minimum?: number | null
  maximum?: number | null
  choices?: string[] | null
  item_type?: string | null
  locked: boolean
}

function buildPackageBItems(): RawSettingItem[] {
  return [
    {
      path: 'orchestration.agentMessages.parentTool',
      type: 'boolean',
      group: 'messages',
      label: 'Pai pode mandar mensagem a subagente',
      description: 'Permite orientações adicionais.',
      scope: 'project_off_only',
      default: true,
      value: true,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'friend.sessionDelivery',
      type: 'choice',
      group: 'friend',
      label: 'Friend: entrega em sessões em andamento',
      description: 'Define entrega no turno ou fila.',
      scope: 'user_only',
      default: 'now',
      value: 'now',
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: ['now', 'queue'],
      locked: false,
    },
    {
      path: 'orchestration.worktree.enabled',
      type: 'boolean',
      group: 'worktree',
      label: 'Worktree por subagente',
      description: 'Executa subagentes com isolation: worktree em uma worktree Git isolada.',
      scope: 'project_off_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.worktree.linkDirs',
      type: 'string_list',
      group: 'worktree',
      label: 'Pastas ligadas na worktree',
      description: 'Diretórios relativos ligados por junção/link simbólico.',
      scope: 'any',
      default: ['node_modules', '.venv'],
      value: ['node_modules', '.venv'],
      layer: 'default',
      user_value: null,
      has_user_override: false,
      item_type: 'relative_dir',
      locked: false,
    },
    {
      path: 'orchestration.worktree.mergeCheckCommand',
      type: 'string',
      group: 'worktree',
      label: 'Comando de checagem da integração',
      description: 'Comando executado após aplicar alterações da worktree.',
      scope: 'any',
      default: null,
      value: null,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.taskBoard.enabled',
      type: 'boolean',
      group: 'task_board',
      label: 'Quadro de tarefas compartilhado',
      description: 'Habilita quadro compartilhado com reivindicação atômica.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.background.autoResume',
      type: 'boolean',
      group: 'background',
      label: 'Retomar agentes após reinício',
      description: 'Retoma automaticamente subagentes interrompidos após reinício.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.stall.minutes',
      type: 'number',
      group: 'background',
      label: 'Considerar agente travado após (minutos)',
      description: 'Tempo sem atividade antes de marcar como travado.',
      scope: 'any',
      default: 10,
      value: 10,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: 1,
      maximum: 1440,
      locked: false,
    },
    {
      path: 'orchestration.stall.autoCancel',
      type: 'boolean',
      group: 'background',
      label: 'Cancelar agente travado automaticamente',
      description: 'Cancela automaticamente subagentes travados.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
  ]
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('OrchestrationSection — Pacote B (worktree, task_board, background) e select Isolamento (WS-B9)', () => {
  it('requests all 6 groups, renders Package B blocks with provenance layers, warns on untrusted workspace, and saves every key via PUT /v1/settings/orchestration', async () => {
    const user = userEvent.setup()
    let items = buildPackageBItems()
    const requestedUrls: string[] = []
    const putPayloads: Array<Record<string, unknown>> = []

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      requestedUrls.push(url)
      if (url.includes('/v1/settings/orchestration')) {
        if (init?.method === 'PUT') {
          const body = JSON.parse(String(init.body ?? '{}')) as { changes: Record<string, unknown> }
          putPayloads.push(body.changes)
          items = items.map((item) => {
            if (!(item.path in body.changes)) return item
            const next = body.changes[item.path]
            if (next === null) {
              return {
                ...item,
                value: item.default,
                user_value: null,
                has_user_override: false,
                layer: 'default',
              }
            }
            return {
              ...item,
              value: next,
              user_value: next,
              has_user_override: true,
              layer: 'user',
            }
          })
        }
        return jsonResponse({
          project_root: '/tmp/untrusted-repo',
          trusted: false,
          items,
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(
      <MemoryRouter initialEntries={['/settings/orchestration']}>
        <OrchestrationSection client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Orquestração' })).toBeInTheDocument()
    expect(
      requestedUrls.some((url) =>
        url.includes('group=messages%2Cfriend%2Cverifier%2Cworktree%2Ctask_board%2Cbackground'),
      ),
    ).toBe(true)

    // All three Package B blocks render
    const worktreeBlock = screen.getByTestId('orchestration-section-worktree')
    const taskBoardBlock = screen.getByTestId('orchestration-section-task-board')
    const backgroundBlock = screen.getByTestId('orchestration-section-background')

    // Untrusted workspace warning for mergeCheckCommand
    expect(within(worktreeBlock).getByTestId('worktree-untrusted-warning')).toHaveTextContent(/não confiável/i)

    // Provenance badges start at Padrão for every Package B key
    for (const key of [
      'orchestration.worktree.enabled',
      'orchestration.worktree.linkDirs',
      'orchestration.worktree.mergeCheckCommand',
      'orchestration.taskBoard.enabled',
      'orchestration.background.autoResume',
      'orchestration.stall.minutes',
      'orchestration.stall.autoCancel',
    ]) {
      expect(screen.getByTestId(`layer-${key}`)).toHaveTextContent('Padrão')
    }

    // 1. Toggle worktree.enabled
    await user.click(within(worktreeBlock).getByRole('switch', { name: /Worktree por subagente/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.worktree.enabled': true })
      expect(screen.getByTestId('layer-orchestration.worktree.enabled')).toHaveTextContent('Usuário')
    })

    // 2. Add linkDir entry
    const linkDirsRow = screen.getByTestId('setting-row-orchestration.worktree.linkDirs')
    const linkInput = within(linkDirsRow).getByRole('textbox')
    await user.type(linkInput, '.cache')
    await user.click(within(linkDirsRow).getByRole('button', { name: /Adicionar/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.worktree.linkDirs': ['node_modules', '.venv', '.cache'],
      })
      expect(screen.getByTestId('layer-orchestration.worktree.linkDirs')).toHaveTextContent('Usuário')
    })

    // 3. Save mergeCheckCommand
    const mergeCmdRow = screen.getByTestId('setting-row-orchestration.worktree.mergeCheckCommand')
    const mergeCmdInput = within(mergeCmdRow).getByRole('textbox')
    await user.type(mergeCmdInput, 'pytest -q')
    await user.click(within(mergeCmdRow).getByRole('button', { name: /Salvar/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.worktree.mergeCheckCommand': 'pytest -q',
      })
      expect(screen.getByTestId('layer-orchestration.worktree.mergeCheckCommand')).toHaveTextContent('Usuário')
    })

    // 4. Toggle taskBoard.enabled
    await user.click(within(taskBoardBlock).getByRole('switch', { name: /Quadro de tarefas compartilhado/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.taskBoard.enabled': true })
      expect(screen.getByTestId('layer-orchestration.taskBoard.enabled')).toHaveTextContent('Usuário')
    })

    // 5. Toggle background.autoResume
    await user.click(within(backgroundBlock).getByRole('switch', { name: /Retomar agentes após reinício/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.background.autoResume': true })
      expect(screen.getByTestId('layer-orchestration.background.autoResume')).toHaveTextContent('Usuário')
    })

    // 6. Update stall.minutes
    const stallMinutesRow = screen.getByTestId('setting-row-orchestration.stall.minutes')
    const stallInput = within(stallMinutesRow).getByRole('spinbutton')
    await user.clear(stallInput)
    await user.type(stallInput, '15')
    await user.click(within(stallMinutesRow).getByRole('button', { name: /Salvar/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.stall.minutes': 15 })
      expect(screen.getByTestId('layer-orchestration.stall.minutes')).toHaveTextContent('Usuário')
    })

    // 7. Toggle stall.autoCancel
    await user.click(within(backgroundBlock).getByRole('switch', { name: /Cancelar agente travado/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.stall.autoCancel': true })
      expect(screen.getByTestId('layer-orchestration.stall.autoCancel')).toHaveTextContent('Usuário')
    })
  })

  it('saves and reloads isolation ("none" | "worktree") in CustomAgentsSection with global-key hint', async () => {
    const user = userEvent.setup()
    let storedAgent = {
      name: 'refactor-bot',
      description: 'Refatora arquivos em paralelo',
      prompt: 'Refatore com cuidado e rode os testes.',
      provider: null,
      models: [],
      tools: null,
      disallowed_tools: [],
      effort: null,
      permission_mode: null,
      max_turns: null,
      memory: 'project',
      isolation: 'none',
      enabled: true,
      updated_at: '2026-11-21T19:00:00Z',
    }
    const savedBodies: Array<Record<string, unknown>> = []

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/v1/providers/')) {
        return jsonResponse({ models: [] })
      }
      if (url.endsWith('/v1/custom-agents') && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ agents: [storedAgent] })
      }
      if (url.includes('/v1/custom-agents/refactor-bot') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>
        savedBodies.push(body)
        storedAgent = {
          ...storedAgent,
          isolation: typeof body.isolation === 'string' ? body.isolation : 'none',
        }
        return jsonResponse({ agent: storedAgent })
      }
      return jsonResponse({ agents: [storedAgent] })
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(
      <MemoryRouter initialEntries={['/library/agents']}>
        <CustomAgentsSection client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByText('@refactor-bot')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Editar' }))

    const isolationSelect = (await screen.findByLabelText(/Isolamento/i)) as HTMLSelectElement
    expect(isolationSelect.value).toBe('none')
    expect(screen.getByText(/efeito só com a chave global/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Orquestração/i })).toHaveAttribute('href', '/settings/orchestration')

    await user.selectOptions(isolationSelect, 'worktree')
    await user.click(screen.getByRole('button', { name: 'Salvar' }))

    await waitFor(() => {
      expect(savedBodies).toHaveLength(1)
      expect(savedBodies[0].isolation).toBe('worktree')
    })
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    expect(((await screen.findByLabelText(/Isolamento/i)) as HTMLSelectElement).value).toBe('worktree')
  })

  it('saves and reloads isolation ("none" | "worktree") in codeSettings/AgentsSection with global-key hint', async () => {
    const user = userEvent.setup()
    let storedAgent = {
      name: 'worker-cli',
      description: 'Executa tarefas isoladas',
      origin: 'user',
      path: '~/.orin/agents/worker-cli.md',
      editable: true,
      prompt: 'Execute as alterações na worktree.',
      tools: ['Read', 'Edit'],
      disallowed_tools: [],
      model: null,
      permission_mode: null,
      max_turns: null,
      effort: null,
      memory: 'project',
      isolation: 'none',
    }
    const savedBodies: Array<Record<string, unknown>> = []

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/v1/providers/')) {
        return jsonResponse({ models: [] })
      }
      if (url.includes('/v1/settings/agents/worker-cli') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>
        savedBodies.push(body)
        storedAgent = {
          ...storedAgent,
          isolation: typeof body.isolation === 'string' ? body.isolation : 'none',
        }
        return jsonResponse({ agents: [storedAgent], errors: [], shadowed: [] })
      }
      if (url.includes('/v1/settings/agents')) {
        return jsonResponse({ agents: [storedAgent], errors: [], shadowed: [] })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(
      <MemoryRouter initialEntries={['/settings/agents']}>
        <AgentsSection client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByText('worker-cli')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Editar' }))

    const isolationSelect = (await screen.findByLabelText(/Isolamento/i)) as HTMLSelectElement
    expect(isolationSelect.value).toBe('none')
    expect(screen.getByText(/efeito só com a chave global/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Orquestração/i })).toHaveAttribute('href', '/settings/orchestration')

    await user.selectOptions(isolationSelect, 'worktree')
    await user.click(screen.getByRole('button', { name: 'Salvar' }))

    await waitFor(() => {
      expect(savedBodies).toHaveLength(1)
      expect(savedBodies[0].isolation).toBe('worktree')
    })

    // Re-open edit form to verify persisted value is reloaded
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    expect(((await screen.findByLabelText(/Isolamento/i)) as HTMLSelectElement).value).toBe('worktree')
  })
})
