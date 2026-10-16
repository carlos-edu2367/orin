import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../../conversations/ChatPage'
import { ProjectPage } from '../ProjectPage'
import { WorkspaceNavigation } from '../WorkspaceNavigation'

function LocationProbe() {
  const loc = useLocation()
  return <output data-testid="current-location">{loc.pathname}{loc.search}</output>
}

describe('Task 3.3: Project & Session Management UI (ChatPage, WorkspaceNavigation, ProjectPage)', () => {
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

  it('moves a standalone session into an existing project or creates a new project from the session in ChatPage header', async () => {
    let currentProjectId: string | null = null
    let currentProjectName: string | null = null
    let patchProjectBody: Record<string, unknown> | null = null
    let createProjectBody: Record<string, unknown> | null = null

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()

      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.endsWith('/v1/projects') && method === 'GET') {
        return new Response(
          JSON.stringify({
            items: [
              {
                project_id: 'proj_alpha',
                name: 'Projeto Alpha',
                description: 'Backend API',
                workspace_id: 'workspace:proj_alpha',
                created_at: null,
                updated_at: null,
                archived_at: null,
              },
            ],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects') && method === 'POST') {
        createProjectBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        currentProjectId = 'proj_created_1'
        currentProjectName = String(createProjectBody.name ?? 'Novo Projeto')
        return new Response(
          JSON.stringify({
            project_id: 'proj_created_1',
            name: currentProjectName,
            description: createProjectBody.description ?? null,
            workspace_id: 'workspace:proj_created_1',
            created_at: null,
            updated_at: null,
            archived_at: null,
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes('/v1/conversations/conv_standalone/project') && method === 'PATCH') {
        patchProjectBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        currentProjectId = (patchProjectBody.project_id as string | null) ?? null
        currentProjectName = currentProjectId ? 'Projeto Alpha' : null
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_standalone',
            project_id: currentProjectId,
            project_name: currentProjectName,
            workspace: {
              kind: 'local',
              path: '/workspace/my-app',
              folder_name: 'my-app',
              scope: currentProjectId ? 'project' : 'chat',
              project_name: currentProjectName,
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/v1/conversations/conv_standalone')) {
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_standalone',
            title: 'Refatorar API de Pagamentos',
            state: 'completed',
            mode: 'code',
            permission_mode: 'accept_edits',
            provider: 'openai',
            model_id: 'gpt-5',
            project_id: currentProjectId,
            project_name: currentProjectName,
            messages: [],
            turns: [],
            activities: [],
            activity_cursor: 'a.1',
            workspace: {
              kind: 'local',
              path: '/workspace/my-app',
              folder_name: 'my-app',
              scope: currentProjectId ? 'project' : 'chat',
              project_name: currentProjectName,
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    render(
      <MemoryRouter initialEntries={['/chats/conv_standalone']}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
          <Route path="/projects/:projectId/chats/:conversationId" element={<ChatPage />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>,
    )

    const trigger = await screen.findByTestId('conversation-project-trigger')
    expect(trigger).toHaveTextContent(/Enviar para projeto/i)
    fireEvent.click(trigger)

    const menu = await screen.findByTestId('conversation-project-menu')
    expect(menu).toBeInTheDocument()

    // Move to existing project "Projeto Alpha"
    const moveBtn = await screen.findByRole('button', { name: /Projeto Alpha/i })
    fireEvent.click(moveBtn)

    await waitFor(() => {
      expect(patchProjectBody).toEqual({ project_id: 'proj_alpha' })
      expect(screen.getByTestId('current-location')).toHaveTextContent(
        '/projects/proj_alpha/chats/conv_standalone',
      )
    })

    // Now the header shows the project link "Projeto Alpha"
    expect(await screen.findByRole('link', { name: 'Projeto Alpha' })).toHaveAttribute(
      'href',
      '/projects/proj_alpha',
    )

    // Open menu again and create a new project from the current session
    fireEvent.click(screen.getByTestId('conversation-project-trigger'))
    const form = await screen.findByTestId('create-project-from-session-form')
    const nameInput = form.querySelector('input[name="project-name"]') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'Pagamentos Core' } })
    fireEvent.submit(form)

    await waitFor(() => {
      expect(createProjectBody).toEqual(
        expect.objectContaining({
          name: 'Pagamentos Core',
          workspace_path: '/workspace/my-app',
          workspace_acknowledged_risk: true,
          source_conversation_id: 'conv_standalone',
        }),
      )
      expect(screen.getByTestId('current-location')).toHaveTextContent(
        '/projects/proj_created_1/chats/conv_standalone',
      )
    })
  })

  it('displays project workspace management, mode badges, and quick New Chat / New Code buttons on ProjectPage', async () => {
    let attachedPath: string | null = '/srv/orin-project'

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()

      if (url.endsWith('/v1/projects/sidebar')) {
        return new Response(
          JSON.stringify({
            items: [
              {
                project_id: 'proj_1',
                name: 'Orin Core',
                description: 'Workspace principal do Orin',
                workspace: {
                  kind: attachedPath ? 'local' : 'managed',
                  path: attachedPath,
                  folder_name: attachedPath ? 'orin-project' : null,
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
                    title: 'Implementar endpoints',
                    state: 'completed',
                    updated_at: null,
                    mode: 'code',
                  },
                ],
              },
            ],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects/proj_1/memories')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/v1/projects/proj_1/workspace') && method === 'PUT') {
        const body = JSON.parse(String(init?.body ?? '{}')) as { path: string }
        attachedPath = body.path
        return new Response(
          JSON.stringify({
            kind: 'local',
            path: attachedPath,
            folder_name: 'nova-pasta',
            scope: 'project',
            project_name: 'Orin Core',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects/proj_1/workspace') && method === 'DELETE') {
        attachedPath = null
        return new Response(
          JSON.stringify({
            kind: 'managed',
            path: null,
            folder_name: null,
            scope: 'project',
            project_name: 'Orin Core',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    render(
      <MemoryRouter initialEntries={['/projects/proj_1']}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Orin Core' })).toBeInTheDocument()

    // Quick action buttons
    const newChatBtn = screen.getByTestId('project-new-chat-button')
    const newCodeBtn = screen.getByTestId('project-new-code-button')
    expect(newChatBtn).toHaveAttribute('href', '/projects/proj_1/new?mode=chat')
    expect(newCodeBtn).toHaveAttribute('href', '/projects/proj_1/new?mode=code')

    // Workspace section shows current path and allows changing or detaching
    const wsSection = screen.getByTestId('project-workspace-section')
    expect(wsSection).toHaveTextContent('/srv/orin-project')

    const wsInput = screen.getByTestId('project-workspace-input') as HTMLInputElement
    fireEvent.change(wsInput, { target: { value: '/srv/nova-pasta' } })
    fireEvent.click(screen.getByTestId('project-workspace-save-button'))

    await waitFor(() => {
      expect(screen.getByTestId('project-workspace-section')).toHaveTextContent('/srv/nova-pasta')
    })

    fireEvent.click(screen.getByTestId('project-workspace-detach-button'))
    await waitFor(() => {
      expect(screen.getByTestId('project-workspace-section')).toHaveTextContent(/Workspace gerenciado/i)
    })

    // Sessions list shows both chats with mode badges
    expect(screen.getByText('Planejamento da arquitetura')).toBeInTheDocument()
    expect(screen.getByText('Implementar endpoints')).toBeInTheDocument()
  })

  it('passes optional workspace_path when creating a new project from WorkspaceNavigation sidebar', async () => {
    let createdBody: Record<string, unknown> | null = null

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/v1/projects') && method === 'POST') {
        createdBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        return new Response(
          JSON.stringify({
            project_id: 'proj_nav_1',
            name: createdBody.name,
            description: createdBody.description,
            workspace_id: 'workspace:proj_nav_1',
            created_at: null,
            updated_at: null,
            archived_at: null,
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    })

    render(
      <MemoryRouter>
        <WorkspaceNavigation />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Projetos' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Novo projeto' }))

    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Projeto Local' } })
    fireEvent.change(screen.getByTestId('new-project-workspace-path'), {
      target: { value: '/home/dev/projeto-local' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Criar' }))

    await waitFor(() => {
      expect(createdBody).toEqual(
        expect.objectContaining({
          name: 'Projeto Local',
          workspace_path: '/home/dev/projeto-local',
          workspace_acknowledged_risk: true,
        }),
      )
    })
  })
})
