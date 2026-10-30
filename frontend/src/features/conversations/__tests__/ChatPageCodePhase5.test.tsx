import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { appRoutes } from '../../../app/routes'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_code_phase5'

function makeActivity(
  sequence: number,
  type: string,
  summary: string,
  payload: Record<string, unknown> = {},
) {
  return {
    event_id: `ev:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-10-30T14:32:00.000Z',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: 'agent:main',
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

describe('Phase 5 Task 4: routes (/projects) and ChatPage Orin Code Split Workbench (ref-08 & ref-16)', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('registers /projects in routes.tsx and renders ProjectsGalleryPage (ref-05.png)', async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.endsWith('/v1/projects/sidebar')) {
        return new Response(
          JSON.stringify({
            items: [
              {
                project_id: 'proj_auryly',
                name: 'Auryly',
                description: 'Produto e conteúdo',
                workspace: {
                  kind: 'local',
                  path: 'C:/Projetos/auryly',
                  folder_name: 'auryly',
                  scope: 'project',
                  project_name: 'Auryly',
                },
                chats: [
                  {
                    conversation_id: 'c1',
                    title: 'Nova página da trilha',
                    state: 'running',
                    updated_at: '2026-10-30T14:10:00.000Z',
                    mode: 'code',
                  },
                ],
              },
            ],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    })

    const router = createMemoryRouter(appRoutes, {
      initialEntries: ['/projects'],
    })

    render(
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>,
    )

    expect(await screen.findByRole('heading', { level: 1, name: 'Projetos' })).toBeInTheDocument()
    expect(screen.getByText('Um espaço para cada trabalho.')).toBeInTheDocument()
    expect(await screen.findByTestId('project-card-proj_auryly')).toBeInTheDocument()
    expect(screen.getByTestId('project-card-active-badge')).toHaveTextContent('1 tarefa em andamento')
  })

  it('renders Orin Code split layout (.chat--code-split), collapsed changed-files card, checklist card, CodeWorkbenchPanel, and opens Time Machine with projectName & filePatches', async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.includes('/file-history')) {
        return new Response(
          JSON.stringify({
            items: [
              {
                message_id: 'msg-1',
                turn_id: 'turn-1',
                kind: 'message',
                created_at: '2026-10-30T14:20:00.000Z',
                rewound_at: null,
                files: ['src/App.tsx', 'src/styles.css'],
              },
            ],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(
          JSON.stringify({
            conversation_id: CONVERSATION_ID,
            title: 'Nova página da trilha',
            state: 'running',
            mode: 'code',
            permission_mode: 'ask_permissions',
            provider: 'openai',
            model_id: 'gpt-5',
            project_id: 'proj_auryly',
            project_name: 'Auryly',
            messages: [
              {
                message_id: 'msg-1',
                role: 'user',
                content: 'Crie uma página para a trilha Ferramentas do Hype.',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-1',
              },
              {
                message_id: 'msg-2',
                role: 'assistant',
                content: 'A estrutura está pronta. Estou ajustando a versão mobile.',
                status: 'streaming',
                retryable: false,
                turn_id: 'turn-1',
              },
            ],
            turns: [
              {
                turn_id: 'turn-1',
                state: 'running',
                code_mode: 'code',
                created_at: '2026-10-30T14:30:00.000Z',
                started_at: '2026-10-30T14:30:00.000Z',
                finished_at: null,
              },
            ],
            activities: [
              makeActivity(1, 'tool.finished', 'Editou src/App.tsx', {
                tool_name: 'edit_file',
                tool_kind: 'filesystem',
                status: 'succeeded',
                path: 'src/App.tsx',
                added_lines: 12,
                removed_lines: 3,
                structured_patch: [
                  {
                    old_start: 12,
                    old_lines: 4,
                    new_start: 12,
                    new_lines: 4,
                    lines: [' return (', '-  <OldTrilha />', '+  <NovaTrilhaHype />', ' )'],
                  },
                ],
              }),
              makeActivity(2, 'tool.finished', 'Editou src/styles.css', {
                tool_name: 'write_file',
                tool_kind: 'filesystem',
                status: 'succeeded',
                path: 'src/styles.css',
                added_lines: 8,
                removed_lines: 1,
              }),
              makeActivity(3, 'tool.finished', 'Rodou servidor dev', {
                tool_name: 'run_command',
                tool_kind: 'terminal',
                status: 'succeeded',
                command: 'npm run dev',
                stdout: 'Local: http://localhost:3000/trilhas/ferramentas-do-hype',
                detected_url: 'http://localhost:3000/trilhas/ferramentas-do-hype',
              }),
              makeActivity(4, 'tool.finished', 'Atualizou tarefas', {
                tool_name: 'todo_write',
                tool_kind: 'filesystem',
                status: 'succeeded',
                todos: [
                  { id: 't1', content: 'Criando estrutura da página', status: 'completed' },
                  { id: 't2', content: 'Aplicando estilos e componentes', status: 'completed' },
                  { id: 't3', content: 'Testando navegação...', status: 'in_progress' },
                ],
              }),
            ],
            activity_cursor: 'a.9',
            workspace: {
              kind: 'local',
              path: 'C:/Projetos/auryly',
              folder_name: 'auryly',
              scope: 'project',
              project_name: 'Auryly',
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    const user = userEvent.setup()
    const { container } = render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[`/projects/proj_auryly/chats/${CONVERSATION_ID}`]}>
          <Routes>
            <Route path="/projects/:projectId/chats/:conversationId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    )

    // 1. Main element has chat--code-split and CodeWorkbenchPanel is a direct child of <main>
    const mainEl = await screen.findByRole('main')
    await waitFor(() => {
      expect(mainEl).toHaveClass('chat', 'chat--code-split')
    })
    const workbench = screen.getByTestId('code-workbench-panel')
    expect(workbench.parentElement).toBe(mainEl)

    // 2. Header preserves exact badge & title contracts and renders History button
    expect(screen.getByTestId('conversation-project-badge')).toHaveTextContent(/^Auryly$/)
    expect(screen.getByRole('heading', { level: 1, name: 'Nova página da trilha' })).toBeInTheDocument()
    const historyBtn = screen.getByTestId('code-open-history-button')
    expect(historyBtn).toHaveTextContent('Histórico')

    // 3. Changed files card is collapsed by default so file paths are NOT exposed before clicking
    const changedCard = screen.getByTestId('code-changed-files-card')
    expect(changedCard).toHaveTextContent('2 arquivos alterados')
    expect(changedCard).not.toHaveTextContent('src/App.tsx')
    expect(workbench).not.toHaveTextContent('src/App.tsx')

    // 4. Steps checklist card shows completed checkmarks and in_progress status
    const checklistCard = screen.getByTestId('code-steps-checklist-card')
    expect(checklistCard).toHaveTextContent('Criando estrutura da página')
    expect(checklistCard).toHaveTextContent('Aplicando estilos e componentes')
    expect(checklistCard).toHaveTextContent('Testando navegação...')
    expect(within(checklistCard).getByText('Em andamento')).toBeInTheDocument()

    // 5. Clicking "Ver alterações" expands the card AND switches CodeWorkbenchPanel to the "Arquivos" tab
    const viewChangesBtn = screen.getByTestId('code-view-changes-button')
    await user.click(viewChangesBtn)
    expect(changedCard).toHaveTextContent('src/App.tsx')
    expect(screen.getByTestId('workbench-tab-files')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('workbench-files-view')).toHaveTextContent('src/App.tsx')
    expect(screen.getByTestId('workbench-file-detail')).toHaveTextContent('<NovaTrilhaHype />')

    // 6. Switching to Terminal tab shows the executed command
    await user.click(screen.getByTestId('workbench-tab-terminal'))
    expect(screen.getByTestId('workbench-terminal-view')).toHaveTextContent('npm run dev')

    // 7. Composer placeholder in Code mode with messages is "Peça um ajuste..."
    const composerInput = screen.getByRole('textbox', { name: 'Mensagem' })
    expect(composerInput).toHaveAttribute('placeholder', 'Peça um ajuste...')

    // 8. Clicking "Histórico" opens the Time Machine (RewindPicker) with projectName="Auryly" and real diff patch
    fireEvent.click(historyBtn)
    const dialog = await screen.findByRole('dialog', { name: 'Voltar a uma mensagem' })
    expect(within(dialog).getByTestId('history-breadcrumb')).toHaveTextContent('Auryly / Histórico')
    expect(within(dialog).getByTestId('history-diff-column')).toHaveTextContent('<NovaTrilhaHype />')
    expect(container.querySelector('.rewind-time-machine')).not.toBeNull()
  })
})
