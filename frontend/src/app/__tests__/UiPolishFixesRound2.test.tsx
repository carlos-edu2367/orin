import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../ThemeContext'
import { RuntimeSettingsPage } from '../../features/settings/RuntimeSettingsPage'
import { AboutSection } from '../../features/settings/AboutSection'
import { WorkspaceNavigation } from '../../features/projects/WorkspaceNavigation'
import type { ApiClient } from '../../api/client'
import stylesheet from '../../styles/agentos.css?raw'

vi.mock('../useSession', () => ({
  useSession: () => ({
    status: 'ready',
    user: { id: 'u-1', name: 'Carlos', role: 'admin' },
    capabilities: {
      ui_updater: true,
      local_workspaces: true,
      profile_files: false,
      multi_user: false,
    },
    refresh: vi.fn(),
  }),
}))

afterEach(() => {
  cleanup()
})

function createMockClient(overrides?: Partial<Record<string, unknown>>): ApiClient {
  return {
    createMutationIntent: () => ({ id: 'intent-1' }),
    request: vi.fn(
      async ({
        path: reqPath,
        method,
        body,
        parse,
      }: {
        path: string
        method?: string
        body?: unknown
        parse?: (raw: unknown) => unknown
      }) => {
        const respond = (raw: unknown) => (typeof parse === 'function' ? parse(raw) : raw)
        if (overrides && reqPath in overrides) {
          const custom = overrides[reqPath]
          const raw = typeof custom === 'function' ? custom({ path: reqPath, method, body }) : custom
          return respond(raw)
        }
        if (reqPath === '/v1/settings/runtime') {
          return respond({ max_iterations: 24, subagent_max_iterations: 48 })
        }
        if (reqPath === '/v1/code-mode/settings' || reqPath === '/v1/settings/code-mode') {
          if (method === 'PUT') return respond(body)
          return respond({
            autonomy: 'approval_required',
            monitoring_enabled: true,
            system_notifications: true,
          })
        }
        if (reqPath === '/v1/installation/status') {
          return respond({
            installation_kind: 'installed',
            current_version: '0.4.0',
            latest_release: { version: '0.5.0', url: 'https://example.com/v0.5.0' },
            latest_release_error: null,
            update_available: true,
            installed_versions: [{ version: '0.4.0', is_current: true, removable: false }],
            checked_at: '2026-10-17T00:00:00Z',
          })
        }
        if (reqPath === '/v1/installation/update/status') {
          return respond({
            state: 'downloading',
            current_version: '0.4.0',
            version: '0.5.0',
            label: 'Baixando a versão 0.5.0',
            progress: 0.42,
            bytes_done: 42 * 1048576,
            bytes_total: 100 * 1048576,
            speed: 5 * 1048576,
            notes: null,
            error: null,
            last_attempt: null,
          })
        }
        if (reqPath === '/v1/workspaces/inspect') {
          return respond({
            path: 'C:\\Users\\carlos\\Documents\\meu-projeto',
            exists: true,
            is_directory: true,
            writable: true,
            entry_count: 12,
            entries_truncated: false,
            risk: 'none',
          })
        }
        if (reqPath === '/v1/conversations') {
          return respond({ items: [] })
        }
        if (reqPath === '/v1/projects/sidebar') {
          return respond({ items: [] })
        }
        return respond({})
      },
    ),
  } as unknown as ApiClient
}

describe('UI Polish Fixes Round 2 (Dropdowns, Update Progress, Project Folder Picker, Sidebar Tabs)', () => {
  it('renders a custom styled dropdown in RuntimeSettingsPage for Autonomia padrão with option descriptions and keyboard navigation', async () => {
    const client = createMockClient()
    render(
      <ThemeProvider>
        <MemoryRouter>
          <RuntimeSettingsPage client={client} embedded />
        </MemoryRouter>
      </ThemeProvider>,
    )

    const trigger = await screen.findByTestId('custom-select-trigger')
    expect(trigger).toBeInTheDocument()
    expect(trigger).toHaveTextContent('Sempre pedir aprovação')

    fireEvent.click(trigger)
    const listbox = await screen.findByRole('listbox')
    expect(listbox).toBeInTheDocument()

    const fullAutonomyOption = screen.getByRole('option', { name: /Autonomia total/i })
    fireEvent.click(fullAutonomyOption)

    expect(trigger).toHaveTextContent('Autonomia total')
  })

  it('renders styled progress card and progressbar in AboutSection during update download', async () => {
    const client = createMockClient()
    render(
      <ThemeProvider>
        <MemoryRouter>
          <AboutSection client={client} />
        </MemoryRouter>
      </ThemeProvider>,
    )

    const progress = await screen.findByRole('progressbar', { name: 'Progresso do download da atualização' })
    expect(progress).toHaveClass('orin-progress')

    expect(stylesheet).toContain('.orin-progress')
    expect(stylesheet).toContain('::-webkit-progress-value')
  })

  it('provides a native folder picker button inside the Criar projeto dialog that populates the workspace path input', async () => {
    const client = createMockClient()
    render(
      <ThemeProvider>
        <MemoryRouter>
          <WorkspaceNavigation client={client} initialView="projects" />
        </MemoryRouter>
      </ThemeProvider>,
    )

    const newProjectBtn = await screen.findByRole('button', { name: /Novo projeto/i })
    fireEvent.click(newProjectBtn)

    const pickFolderBtn = await screen.findByTestId('new-project-pick-folder')
    expect(pickFolderBtn).toBeInTheDocument()

    fireEvent.click(pickFolderBtn)

    const pathInput = screen.getByTestId('new-project-workspace-path') as HTMLInputElement
    await waitFor(() => {
      expect(pathInput.value).toBe('C:\\Users\\carlos\\Documents\\meu-projeto')
    })
  })

  it('renders sidebar Chats and Projetos selector as an underline tab bar with icons and empty state icon', async () => {
    const client = createMockClient()
    render(
      <ThemeProvider>
        <MemoryRouter>
          <WorkspaceNavigation client={client} />
        </MemoryRouter>
      </ThemeProvider>,
    )

    expect(await screen.findByTestId('sidebar-tab-icon-chats')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-tab-icon-projects')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-empty-chats-icon')).toBeInTheDocument()
  })

  it('stops Escape propagation when closing CustomSelect menu so parent modal does not close (adversarial)', async () => {
    const client = createMockClient()
    const parentEscapeSpy = vi.fn()
    window.addEventListener('keydown', parentEscapeSpy)

    try {
      render(
        <ThemeProvider>
          <MemoryRouter>
            <RuntimeSettingsPage client={client} embedded />
          </MemoryRouter>
        </ThemeProvider>,
      )

      const trigger = await screen.findByTestId('custom-select-trigger')
      fireEvent.click(trigger)
      expect(await screen.findByRole('listbox')).toBeInTheDocument()

      fireEvent.keyDown(window, { key: 'Escape' })
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
      expect(parentEscapeSpy).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('keydown', parentEscapeSpy)
    }
  })

  it('preserves existing workspace path if user cancels the native folder picker dialog (adversarial)', async () => {
    const client = createMockClient({
      '/v1/workspaces/inspect': { cancelled: true },
    })
    render(
      <ThemeProvider>
        <MemoryRouter>
          <WorkspaceNavigation client={client} initialView="projects" />
        </MemoryRouter>
      </ThemeProvider>,
    )

    fireEvent.click(await screen.findByRole('button', { name: /Novo projeto/i }))
    const pathInput = screen.getByTestId('new-project-workspace-path') as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: 'D:\\manter-caminho' } })

    const pickFolderBtn = screen.getByTestId('new-project-pick-folder')
    fireEvent.click(pickFolderBtn)

    await waitFor(() => {
      expect(pickFolderBtn).not.toBeDisabled()
    })
    expect(pathInput.value).toBe('D:\\manter-caminho')
  })
})

