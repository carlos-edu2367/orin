import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { Home } from '../../../app/Home'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ProjectPage } from '../ProjectPage'
import { ProjectsGalleryPage } from '../ProjectsGalleryPage'

function LocationProbe() {
  const loc = useLocation()
  return <output data-testid="current-location">{loc.pathname}{loc.search}</output>
}

describe('Phase 5 Task 1: ProjectsGalleryPage (ref-05) & ProjectPage Context Dashboard (ref-07)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders ProjectsGalleryPage (ref-05) with search filter, active task badge, counts, sidebar link, and new project creation', async () => {
    let createdBody: Record<string, unknown> | null = null
    const nowIso = new Date(Date.now() - 20 * 60 * 1000).toISOString()

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
        const method = (init?.method ?? 'GET').toUpperCase()

        if (url.endsWith('/v1/projects') && method === 'POST') {
          createdBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
          return new Response(
            JSON.stringify({
              project_id: 'proj_new',
              name: String(createdBody.name ?? 'Novo'),
              description: (createdBody.description as string | null) ?? null,
              workspace_id: 'ws_new',
              created_at: nowIso,
              updated_at: nowIso,
              archived_at: null,
            }),
            { status: 201, headers: { 'Content-Type': 'application/json' } },
          )
        }

        if (url.includes('/v1/projects/sidebar')) {
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
                      updated_at: nowIso,
                      mode: 'code',
                    },
                    {
                      conversation_id: 'c2',
                      title: 'Estratégia de lançamento',
                      state: 'completed',
                      updated_at: nowIso,
                      mode: 'chat',
                    },
                  ],
                },
                {
                  project_id: 'proj_fin',
                  name: 'Financeiro',
                  description: 'Organização pessoal',
                  chats: [
                    {
                      conversation_id: 'c3',
                      title: 'Planilha mensal',
                      state: 'completed',
                      updated_at: nowIso,
                      mode: 'chat',
                    },
                  ],
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }

        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    })

    const { container } = render(
      <MemoryRouter initialEntries={['/projects']}>
        <ThemeProvider>
          <Routes>
            <Route path="/projects" element={<ProjectsGalleryPage client={client} />} />
          </Routes>
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Projetos' })).toBeInTheDocument()
    expect(screen.getByText('Um espaço para cada trabalho.')).toBeInTheDocument()

    // Sidebar view-all-projects link is present in the projects tab
    const viewAllLink = await screen.findByTestId('sidebar-view-all-projects')
    expect(viewAllLink).toHaveAttribute('href', '/projects')

    // Cards rendered in grid
    const grid = await screen.findByTestId('projects-catalog-grid')
    const aurylyCard = await within(grid).findByTestId('project-card-proj_auryly')
    const finCard = await within(grid).findByTestId('project-card-proj_fin')

    expect(within(aurylyCard).getByRole('heading', { name: 'Auryly' })).toBeInTheDocument()
    expect(within(aurylyCard).getByTestId('project-card-active-badge')).toHaveTextContent(/1 tarefa em andamento/i)
    expect(within(aurylyCard).getByTestId('project-card-counts')).toHaveTextContent(/2 conversas\s*·\s*\d+ arquivos/i)
    expect(within(aurylyCard).getByTestId('project-card-open-link')).toHaveAttribute('href', '/projects/proj_auryly')

    // Financeiro has no running task badge
    expect(within(finCard).queryByTestId('project-card-active-badge')).not.toBeInTheDocument()

    // Search filters projects by name and description
    const searchInput = screen.getByTestId('projects-search-input')
    fireEvent.change(searchInput, { target: { value: 'organização' } })
    expect(within(grid).queryByTestId('project-card-proj_auryly')).not.toBeInTheDocument()
    expect(within(grid).getByTestId('project-card-proj_fin')).toBeInTheDocument()

    fireEvent.change(searchInput, { target: { value: '' } })
    expect(within(grid).getByTestId('project-card-proj_auryly')).toBeInTheDocument()

    // Create new project via inline/modal form
    fireEvent.click(screen.getByTestId('projects-new-button'))
    const form = await screen.findByTestId('projects-create-form')
    fireEvent.change(within(form).getByLabelText(/Nome/i), { target: { value: 'Estudos' } })
    fireEvent.change(within(form).getByLabelText(/Descrição/i), { target: { value: 'Aprendizado e referências' } })
    fireEvent.submit(form)

    await waitFor(() => {
      expect(createdBody).toEqual(
        expect.objectContaining({
          name: 'Estudos',
          description: 'Aprendizado e referências',
        }),
      )
    })

    // Zero hardcoded dark utility classes
    for (const el of Array.from(container.querySelectorAll('*'))) {
      const cls = typeof el.className === 'string' ? el.className : ''
      expect(cls).not.toMatch(/\b(text-white|bg-black|border-white)\b/)
    }
  })

  it('renders ProjectPage (ref-07) with single h1 heading, breadcrumb, 4 tabs, inline composer prefill to Home, shared context panel, and resilient instructions editor', async () => {
    // Seed corrupted localStorage for instructions to verify resilient fallback
    window.localStorage.setItem('orin.project.instructions.v1.proj_1', '{corrupted-json')

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
        if (url.includes('/v1/projects/sidebar')) {
          return new Response(
            JSON.stringify({
              items: [
                {
                  project_id: 'proj_1',
                  name: 'Orin Core',
                  description: 'Produto, conteúdo e automações.',
                  workspace: {
                    kind: 'local',
                    path: 'C:/Projetos/orin-core',
                    folder_name: 'orin-core',
                    scope: 'project',
                    project_name: 'Orin Core',
                  },
                  chats: [
                    {
                      conversation_id: 'chat_1',
                      title: 'Planejamento da arquitetura',
                      state: 'completed',
                      updated_at: null,
                      mode: 'chat',
                    },
                    {
                      conversation_id: 'code_1',
                      title: 'Nova página da trilha',
                      state: 'running',
                      updated_at: null,
                      mode: 'code',
                    },
                  ],
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        if (url.includes('/v1/projects/proj_1/memories')) {
          return new Response(
            JSON.stringify({
              items: [{ memory_id: 'm1', fact: 'Usar TypeScript estrito', tags: [], updated_at: null }],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        if (url.includes('/models')) {
          return new Response(
            JSON.stringify({
              items: [{ model_id: 'gpt-5', display_name: 'GPT-5', is_favorite: true }],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    })

    const { container } = render(
      <MemoryRouter initialEntries={['/projects/proj_1']}>
        <ThemeProvider>
          <Routes>
            <Route path="/projects/:projectId" element={<ProjectPage client={client} />} />
            <Route
              path="/projects/:projectId/new"
              element={<Home client={client} bootstrap={{ status: 'ready', csrfToken: 'tok' }} />}
            />
          </Routes>
          <LocationProbe />
        </ThemeProvider>
      </MemoryRouter>,
    )

    // Only ONE heading with the project name "Orin Core"
    const headings = await screen.findAllByRole('heading', { name: 'Orin Core' })
    expect(headings).toHaveLength(1)
    expect(headings[0].tagName).toBe('H1')

    // Breadcrumb uses <strong>, not heading
    const breadcrumb = screen.getByTestId('project-breadcrumb')
    expect(breadcrumb).toHaveTextContent(/Projetos\s*\/\s*Orin Core/i)
    expect(within(breadcrumb).getByRole('link', { name: 'Projetos' })).toHaveAttribute('href', '/projects')

    // Settings button & 4 tabs
    expect(screen.getByTestId('project-settings-button')).toBeInTheDocument()
    const tabs = screen.getByTestId('project-tabs')
    expect(within(tabs).getByTestId('project-tab-conversations')).toBeInTheDocument()
    expect(within(tabs).getByTestId('project-tab-files')).toBeInTheDocument()
    expect(within(tabs).getByTestId('project-tab-instructions')).toBeInTheDocument()
    expect(within(tabs).getByTestId('project-tab-memory')).toBeInTheDocument()

    // Legacy quick action buttons & workspace section still visible in default Conversas tab
    expect(screen.getByTestId('project-new-chat-button')).toHaveAttribute('href', '/projects/proj_1/new?mode=chat')
    expect(screen.getByTestId('project-new-code-button')).toHaveAttribute('href', '/projects/proj_1/new?mode=code')
    expect(screen.getByTestId('project-workspace-section')).toHaveTextContent('C:/Projetos/orin-core')
    expect(screen.getByTestId('project-chat-code-pill')).toBeInTheDocument()

    // Conversation titles are not duplicated in sidebar because hideProjectChatSublist={true}
    expect(screen.getAllByText('Planejamento da arquitetura')).toHaveLength(1)

    // Search conversations filter
    const chatSearch = screen.getByTestId('project-chats-search')
    fireEvent.change(chatSearch, { target: { value: 'trilha' } })
    expect(screen.queryByText('Planejamento da arquitetura')).not.toBeInTheDocument()
    expect(screen.getByText('Nova página da trilha')).toBeInTheDocument()
    fireEvent.change(chatSearch, { target: { value: '' } })
    expect(screen.getByText('Planejamento da arquitetura')).toBeInTheDocument()

    // Right column shared context
    const sharedContext = screen.getByTestId('project-shared-context')
    expect(within(sharedContext).getByTestId('project-context-folder-card')).toHaveTextContent('C:/Projetos/orin-core')
    expect(within(sharedContext).getByTestId('project-context-instructions-card')).toBeInTheDocument()
    expect(within(sharedContext).getByTestId('project-add-files-button')).toBeInTheDocument()
    expect(within(sharedContext).getByTestId('project-edit-instructions-button')).toBeInTheDocument()

    // Edit instructions via shared context button -> opens Instruções tab & persists safely
    fireEvent.click(within(sharedContext).getByTestId('project-edit-instructions-button'))
    const editor = await screen.findByTestId('project-instructions-editor')
    const instructionInput = within(editor).getByPlaceholderText(/Adicionar instrução/i)
    fireEvent.change(instructionInput, { target: { value: 'Sempre escrever testes TDD primeiro.' } })
    fireEvent.submit(editor.querySelector('form')!)

    expect(await within(editor).findByText('Sempre escrever testes TDD primeiro.')).toBeInTheDocument()
    expect(JSON.parse(window.localStorage.getItem('orin.project.instructions.v1.proj_1') ?? '[]')).toContain(
      'Sempre escrever testes TDD primeiro.',
    )

    // Zero hardcoded dark utility classes
    for (const el of Array.from(container.querySelectorAll('*'))) {
      const cls = typeof el.className === 'string' ? el.className : ''
      expect(cls).not.toMatch(/\b(text-white|bg-black|border-white)\b/)
    }

    // Switch back to Conversas tab and submit inline composer -> navigates to Home with prompt prefilled
    fireEvent.click(screen.getByTestId('project-tab-conversations'))
    const composer = screen.getByTestId('project-inline-composer')
    const composerInput = within(composer).getByTestId('project-composer-input')
    const composerMode = within(composer).getByTestId('project-composer-mode')
    fireEvent.change(composerMode, { target: { value: 'code' } })
    fireEvent.change(composerInput, { target: { value: 'Criar rota de relatórios' } })
    fireEvent.click(within(composer).getByTestId('project-composer-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('current-location')).toHaveTextContent(
        '/projects/proj_1/new?mode=code&prompt=Criar%20rota%20de%20relat%C3%B3rios',
      )
    })

    // Home initializes its composer input from the prompt query param
    expect(await screen.findByDisplayValue('Criar rota de relatórios')).toBeInTheDocument()
  })
})
