import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import { ThemeProvider } from '../ThemeContext'
import { WorkspaceShell } from '../WorkspaceShell'

function createStubClient(): ApiClient {
  const fetchImpl = vi.fn<typeof fetch>(async () => {
    return new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  return new ApiClient({ fetchImpl, maxAttempts: 1 })
}

describe('WorkspaceShell & Sidebar Hubs', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-accent')
    document.documentElement.removeAttribute('data-ambient')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders full-height sidebar with hubs, collapses to rail, cycles theme, and opens PreferencesModal from footer', async () => {
    const user = userEvent.setup()
    const client = createStubClient()

    const { container } = render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell
            client={client}
            headerLeft={<div data-testid="slot-left">Model Pill</div>}
            headerCenter={<div data-testid="slot-center">Title</div>}
            headerRight={<div data-testid="slot-right">Actions</div>}
          >
            <div data-testid="canvas-content">Main Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )

    const header = screen.getByTestId('canvas-header')
    expect(header).toBeInTheDocument()
    expect(screen.getByTestId('slot-left')).toBeInTheDocument()
    expect(screen.getByTestId('slot-center')).toBeInTheDocument()
    expect(screen.getByTestId('slot-right')).toBeInTheDocument()
    expect(screen.getByTestId('canvas-content')).toBeInTheDocument()
    expect(container.querySelector('.workspace-shell__canvas')).not.toBeNull()
    expect(container.querySelector('.canvas-stage')).not.toBeNull()

    // Primary CTA "+ Nova conversa" and primary nav items ("Início", "Friend", "Ações agendadas")
    const newConvBtn = screen.getByTestId('sidebar-new-chat')
    expect(newConvBtn).toHaveTextContent(/\+\s*Nova conversa/i)
    expect(screen.getByRole('link', { name: /^Início$/i })).toHaveAttribute('href', '/')
    expect(screen.getByTestId('sidebar-orin-friend-button')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Ações agendadas/i })).toHaveAttribute('href', '/schedules')

    // Sidebar footer links ("Habilidades", "Conexões", "Configurações") and local ProfileMenu
    expect(screen.queryByRole('heading', { name: /Seu espaço/i })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Habilidades/i })).toHaveAttribute('href', '/library/skills')
    expect(screen.getByRole('link', { name: /Conexões/i })).toHaveAttribute('href', '/integrations/providers')
    expect(screen.getByRole('link', { name: /Configurações/i })).toHaveAttribute('href', '/settings/general')
    expect(screen.getByTestId('sidebar-profile-menu')).toBeInTheDocument()

    // Topbar right includes "Conversar por voz" button and OrinMascot (size 30) with online status dot
    const voiceBtn = screen.getByTestId('topbar-voice-button')
    expect(voiceBtn).toHaveTextContent(/Conversar por voz/i)
    expect(header.querySelector('[data-testid="orin-activity-center-trigger"]')).not.toBeNull()
    expect(header.querySelector('[data-testid="orin-mascot-status-dot"]')).toBeNull()
    expect(header.querySelector('[data-testid="orin-mascot"]')).toHaveStyle({ width: '30px', height: '30px' })

    // Local ProfileMenu defaults to "Você" when orin.user.displayName is unset, and closes on Escape / outside click
    const profileTrigger = screen.getByTestId('sidebar-profile-trigger')
    expect(profileTrigger).toHaveTextContent(/Você/i)
    await user.click(profileTrigger)
    expect(screen.getByRole('menu', { name: /Conta/i })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu', { name: /Conta/i })).not.toBeInTheDocument()

    await user.click(profileTrigger)
    expect(screen.getByRole('menu', { name: /Conta/i })).toBeInTheDocument()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('menu', { name: /Conta/i })).not.toBeInTheDocument()

    // Toggle sidebar rail collapse and persist in localStorage
    const shell = container.querySelector('.workspace-shell')
    const railToggle = screen.getByTestId('sidebar-rail-toggle')
    expect(railToggle).toHaveAttribute('aria-expanded', 'true')
    expect(shell).toHaveAttribute('data-rail-collapsed', 'false')

    await user.click(railToggle)
    expect(railToggle).toHaveAttribute('aria-expanded', 'false')
    expect(shell).toHaveAttribute('data-rail-collapsed', 'true')
    expect(window.localStorage.getItem('orin.sidebar.collapsed')).toBe('true')

    await user.click(railToggle)
    expect(railToggle).toHaveAttribute('aria-expanded', 'true')
    expect(shell).toHaveAttribute('data-rail-collapsed', 'false')
    expect(window.localStorage.getItem('orin.sidebar.collapsed')).toBe('false')

    // Theme quick toggle cycles theme mode (dark -> light -> carbon)
    const themeToggle = screen.getByTestId('theme-quick-toggle')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    await user.click(themeToggle)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    await user.click(themeToggle)
    expect(document.documentElement.getAttribute('data-theme')).toBe('carbon')

    // Open PreferencesModal from sidebar footer
    await user.click(screen.getByTestId('open-preferences-modal'))
    expect(screen.getByRole('dialog', { name: /Preferências/i })).toBeInTheDocument()
  })

  it('opens PreferencesModal via Ctrl+, and Meta+, keyboard shortcuts and closes on Escape', async () => {
    const client = createStubClient()

    render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell client={client}>
            <div>Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(screen.queryByRole('dialog', { name: /Preferências/i })).not.toBeInTheDocument()

    // Ctrl+, opens PreferencesModal
    fireEvent.keyDown(window, { key: ',', ctrlKey: true })
    expect(screen.getByRole('dialog', { name: /Preferências/i })).toBeInTheDocument()

    // Escape closes PreferencesModal
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: /Preferências/i })).not.toBeInTheDocument()

    // Meta+, also opens PreferencesModal
    fireEvent.keyDown(window, { key: ',', metaKey: true })
    expect(screen.getByRole('dialog', { name: /Preferências/i })).toBeInTheDocument()
  })

  it('restores rail collapsed state from localStorage and falls back safely on invalid or throwing storage', () => {
    const client = createStubClient()
    window.localStorage.setItem('orin.sidebar.collapsed', 'true')

    const { container, unmount } = render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell client={client}>
            <div>Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(container.querySelector('.workspace-shell')).toHaveAttribute('data-rail-collapsed', 'true')
    expect(screen.getByTestId('sidebar-rail-toggle')).toHaveAttribute('aria-expanded', 'false')
    unmount()

    // Corrupted storage value falls back to expanded ('false')
    window.localStorage.setItem('orin.sidebar.collapsed', 'not-a-boolean')
    const { container: container2, unmount: unmount2 } = render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell client={client}>
            <div>Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )
    expect(container2.querySelector('.workspace-shell')).toHaveAttribute('data-rail-collapsed', 'false')
    expect(screen.getByTestId('sidebar-rail-toggle')).toHaveAttribute('aria-expanded', 'true')
    unmount2()

    // Storage throwing SecurityError does not crash render or toggle
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })

    const { container: container3 } = render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell client={client}>
            <div>Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )
    expect(container3.querySelector('.workspace-shell')).toHaveAttribute('data-rail-collapsed', 'false')
    fireEvent.click(screen.getByTestId('sidebar-rail-toggle'))
    expect(container3.querySelector('.workspace-shell')).toHaveAttribute('data-rail-collapsed', 'true')
  })

  it('contains zero hardcoded dark utility classes (text-white, bg-black, border-white) in shell and sidebar', () => {
    const client = createStubClient()
    const { container } = render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell client={client}>
            <div>Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )

    const allElements = Array.from(container.querySelectorAll('*'))
    for (const el of allElements) {
      const cls = typeof el.className === 'string' ? el.className : ''
      expect(cls).not.toMatch(/\b(text-white|bg-black|border-white)\b/)
    }
  })

  it('renders relative time on conversations, folder icon + conversation count on projects, and closes Criar projeto dialog on Escape', async () => {
    const user = userEvent.setup()
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    const client = new ApiClient({
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
        if (url.includes('/v1/conversations')) {
          return new Response(
            JSON.stringify({
              items: [
                {
                  conversation_id: 'chat-rel-1',
                  title: 'Planejamento semanal',
                  state: 'completed',
                  updated_at: twoHoursAgo,
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        if (url.includes('/v1/projects/sidebar')) {
          return new Response(
            JSON.stringify({
              items: [
                {
                  project_id: 'proj-rel-1',
                  name: 'Auryly',
                  description: null,
                  chats: [
                    { conversation_id: 'c1', title: 'Trilha 1', state: 'completed', updated_at: twoHoursAgo },
                    { conversation_id: 'c2', title: 'Trilha 2', state: 'completed', updated_at: twoHoursAgo },
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
      maxAttempts: 1,
    })

    render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell client={client}>
            <div>Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )

    const chatTime = await screen.findByTestId('conversation-time-chat-rel-1')
    expect(chatTime).toHaveTextContent(/há 2\s*h/i)

    await user.click(screen.getByRole('tab', { name: 'Projetos' }))
    const projectCount = await screen.findByTestId('project-count-proj-rel-1')
    expect(projectCount).toHaveTextContent(/2 conversas/i)
    expect(screen.getByTestId('project-folder-icon-proj-rel-1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Novo projeto' }))
    expect(screen.getByRole('dialog', { name: 'Criar projeto' })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Criar projeto' })).not.toBeInTheDocument()
  })
})
