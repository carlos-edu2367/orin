import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { ProviderName } from '../../../api/providers'
import { ThemeProvider } from '../../../app/ThemeContext'
import { AboutSection } from '../AboutSection'
import { IntegrationsHubPage, type IntegrationsTab } from '../IntegrationsHubPage'
import { LibraryHubPage, type LibraryTab } from '../LibraryHubPage'
import { PreferencesModal, type PreferencesTab } from '../PreferencesModal'
import { resetSettingsBadgeCache, useSettingsBadges } from '../useSettingsBadges'


function createMockClient() {
  const fetchImpl = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    if (url.includes('/v1/runtime/settings')) {
      return Promise.resolve(
        new Response(JSON.stringify({ max_iterations: 24, subagent_max_iterations: 48 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/code-mode/settings')) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            autonomy: 'approval_required',
            monitoring_enabled: true,
            system_notifications: false,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }
    if (url.includes('/v1/installation')) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            installation_kind: 'development',
            current_version: '0.1.12',
            installed_versions: [],
            latest_release: null,
            latest_release_error: null,
            update_available: false,
            checked_at: '2026-10-17T00:00:00Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }
    if (url.includes('/v1/skills')) {
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/plugins')) {
      return Promise.resolve(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/mcp/servers')) {
      return Promise.resolve(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/memory')) {
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/schedules')) {
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/vision-model')) {
      return Promise.resolve(
        new Response(JSON.stringify({ provider: null, model_id: null, mode: 'automatic' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.endsWith('/keys')) {
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.endsWith('/models')) {
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.includes('/v1/providers/')) {
      return Promise.resolve(
        new Response(JSON.stringify({ provider: 'openai', enabled: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    return Promise.resolve(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })

  return {
    client: new ApiClient({
      fetchImpl,
      csrfToken: 'test-csrf',
      maxAttempts: 1,
      createIdempotencyKey: () => 'test-idempotency',
    }),
    fetchImpl,
  }
}

describe('PreferencesModal & Sidebar Hubs', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-accent')
    document.documentElement.removeAttribute('data-ambient')
    resetSettingsBadgeCache()
  })

  it('returns null when open is false', () => {
    const { client } = createMockClient()
    render(
      <MemoryRouter>
        <ThemeProvider>
          <PreferencesModal open={false} onClose={vi.fn()} client={client} />
        </ThemeProvider>
      </MemoryRouter>,
    )
    expect(screen.queryByRole('dialog', { name: /Preferências/i })).toBeNull()
  })

  it('switches theme mode, accent, and ambient in real time inside PreferencesModal and switches between the 4 tabs', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const { client } = createMockClient()

    render(
      <MemoryRouter>
        <ThemeProvider>
          <PreferencesModal open={true} initialTab="appearance" onClose={onClose} client={client} />
        </ThemeProvider>
      </MemoryRouter>,
    )

    const dialog = screen.getByRole('dialog', { name: /Preferências/i })
    expect(dialog).toBeInTheDocument()

    // Select Warm Paper (Light) theme
    await user.click(screen.getByTestId('theme-mode-light'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    // Select Carbon OLED theme
    await user.click(screen.getByTestId('theme-mode-carbon'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('carbon')

    // Select Space Dark theme
    await user.click(screen.getByTestId('theme-mode-dark'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    // Select System theme
    await user.click(screen.getByTestId('theme-mode-system'))
    expect(document.documentElement.getAttribute('data-theme-preference')).toBe('system')

    // Select Emerald accent
    await user.click(screen.getByTestId('theme-accent-emerald'))
    expect(document.documentElement.getAttribute('data-accent')).toBe('emerald')

    // Toggle ambient off
    await user.click(screen.getByTestId('theme-ambient-toggle'))
    expect(document.documentElement.getAttribute('data-ambient')).toBe('off')

    // Navigate across the 4 tabs
    const generalTab = screen.getByRole('tab', { name: /Geral & Modo Code/i })
    await user.click(generalTab)
    expect(generalTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText(/Interações do agente/i)).toBeInTheDocument()

    const systemTab = screen.getByRole('tab', { name: /Sistema & Ambiente/i })
    await user.click(systemTab)
    expect(systemTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: /Workspaces locais/i })).toBeInTheDocument()

    const aboutTab = screen.getByRole('tab', { name: /Conta & Sobre/i })
    await user.click(aboutTab)
    expect(aboutTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: /Estado do Orin/i })).toBeInTheDocument()
  })

  it('closes PreferencesModal on Escape and stops propagation to parent listeners', () => {
    const onClose = vi.fn()
    const parentKeyDown = vi.fn()
    const { client } = createMockClient()

    render(
      <div onKeyDown={parentKeyDown}>
        <MemoryRouter>
          <ThemeProvider>
            <PreferencesModal open={true} initialTab="appearance" onClose={onClose} client={client} />
          </ThemeProvider>
        </MemoryRouter>
      </div>,
    )

    const dialog = screen.getByRole('dialog', { name: /Preferências/i })
    fireEvent.keyDown(dialog, { key: 'Escape', bubbles: true })

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(parentKeyDown).not.toHaveBeenCalled()
  })

  it('renders LibraryHubPage with Skills, Plugins, and Memória tabs and switches tabs', async () => {
    const user = userEvent.setup()
    const { client } = createMockClient()

    render(
      <MemoryRouter initialEntries={['/library/skills']}>
        <LibraryHubPage activeTab="skills" client={client} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: /Biblioteca/i })).toBeInTheDocument()
    const skillsTab = screen.getByRole('tab', { name: /Skills/i })
    const pluginsTab = screen.getByRole('tab', { name: /Plugins/i })
    const memoryTab = screen.getByRole('tab', { name: /Memória/i })

    expect(skillsTab).toHaveAttribute('aria-selected', 'true')
    expect(pluginsTab).toHaveAttribute('aria-selected', 'false')
    expect(memoryTab).toHaveAttribute('aria-selected', 'false')

    await user.click(pluginsTab)
    expect(pluginsTab).toHaveAttribute('aria-selected', 'true')

    await user.click(memoryTab)
    expect(memoryTab).toHaveAttribute('aria-selected', 'true')
  })

  it('renders IntegrationsHubPage with tabs and opens ProviderDetail inside a right Slide-Over drawer that traps Escape', async () => {
    const user = userEvent.setup()
    const parentKeyDown = vi.fn()
    const { client } = createMockClient()

    render(
      <div onKeyDown={parentKeyDown}>
        <MemoryRouter initialEntries={['/integrations/providers/openai']}>
          <IntegrationsHubPage activeTab="providers" selectedProvider="openai" client={client} />
        </MemoryRouter>
      </div>,
    )

    expect(screen.getByRole('heading', { name: /Integrações/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Providers & Modelos/i })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Servidores MCP/i })).toHaveAttribute('aria-selected', 'false')

    // ProviderDetail is rendered inside the slide-over drawer slot
    const drawerSlot = screen.getByTestId('settings-drawer-slot')
    expect(drawerSlot).toBeInTheDocument()
    expect(drawerSlot).toHaveClass('slide-over-drawer')
    expect(within(drawerSlot).getByRole('region', { name: 'OpenAI' })).toBeInTheDocument()

    // Pressing Escape inside the drawer closes it and calls stopPropagation
    fireEvent.keyDown(drawerSlot, { key: 'Escape', bubbles: true })
    expect(parentKeyDown).not.toHaveBeenCalled()
    expect(screen.queryByTestId('settings-drawer-slot')).toBeNull()

    // Switching to MCP tab renders MCP section
    await user.click(screen.getByRole('tab', { name: /Servidores MCP/i }))
    expect(screen.getByRole('tab', { name: /Servidores MCP/i })).toHaveAttribute('aria-selected', 'true')
  })

  it('closes only PreferencesModal on Escape when stacked above IntegrationsHubPage slide-over drawer', () => {
    const onCloseModal = vi.fn()
    const { client } = createMockClient()

    render(
      <MemoryRouter initialEntries={['/integrations/providers/openai']}>
        <ThemeProvider>
          <IntegrationsHubPage activeTab="providers" selectedProvider="openai" client={client} />
          <PreferencesModal open={true} initialTab="appearance" onClose={onCloseModal} client={client} />
        </ThemeProvider>
      </MemoryRouter>,
    )

    // Both drawer and modal are mounted
    expect(screen.getByTestId('settings-drawer-slot')).toBeInTheDocument()
    const dialog = screen.getByRole('dialog', { name: /Preferências/i })

    // Escape on document.body or inside dialog closes only PreferencesModal, leaving drawer open
    fireEvent.keyDown(document.body, { key: 'Escape', bubbles: true })
    expect(onCloseModal).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('settings-drawer-slot')).toBeInTheDocument()

    fireEvent.keyDown(dialog, { key: 'Escape', bubbles: true })
    expect(onCloseModal).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('settings-drawer-slot')).toBeInTheDocument()
  })

  it('falls back safely when invalid tab or provider props are supplied at runtime', () => {
    const { client } = createMockClient()

    render(
      <MemoryRouter initialEntries={['/integrations/providers/nonexistent']}>
        <ThemeProvider>
          <PreferencesModal
            open={true}
            initialTab={'invalid-tab' as PreferencesTab}
            onClose={vi.fn()}
            client={client}
          />
          <LibraryHubPage activeTab={'invalid-lib' as LibraryTab} client={client} />
          <IntegrationsHubPage
            activeTab={'invalid-int' as IntegrationsTab}
            selectedProvider={'invalid-provider' as ProviderName}
            client={client}
          />
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(screen.getByTestId('preferences-tab-appearance')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('library-tab-skills')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('integrations-tab-providers')).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByTestId('settings-drawer-slot')).toBeNull()
  })

  it('deduplicates concurrent useSettingsBadges calls across multiple mounted consumers', async () => {
    const { client, fetchImpl } = createMockClient()

    function BadgeConsumer() {
      const badges = useSettingsBadges(client)
      return <span data-testid="badge-consumer">{badges.version?.value ?? 'loading'}</span>
    }

    render(
      <div>
        <BadgeConsumer />
        <BadgeConsumer />
      </div>,
    )

    await screen.findAllByText('v0.1.12')
    const installationCalls = fetchImpl.mock.calls.filter(([input]) => String(input).includes('/v1/installation'))
    expect(installationCalls).toHaveLength(1)
  })

  it('shows a version-deletion specific alert in AboutSection when removing an old version fails', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.includes('/v1/installation/versions/') && init?.method === 'DELETE') {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: 'locked' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      if (url.includes('/v1/installation/status')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              installation_kind: 'installed',
              current_version: '0.8.0',
              installed_versions: [
                { version: '0.8.0', is_current: true, removable: false },
                { version: '0.7.8', is_current: false, removable: true },
              ],
              latest_release: { version: '0.8.0', url: 'https://github.com/carlos-edu2367/orin/releases/tag/v0.8.0' },
              latest_release_error: null,
              update_available: false,
              checked_at: '2026-10-17T00:00:00Z',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      }
      return Promise.resolve(
        new Response(JSON.stringify({ state: 'idle' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })

    const client = new ApiClient({
      fetchImpl,
      csrfToken: 'test-csrf',
      maxAttempts: 1,
      createIdempotencyKey: () => 'test-idempotency',
    })

    render(
      <MemoryRouter>
        <AboutSection client={client} />
      </MemoryRouter>,
    )

    const deleteBtn = await screen.findByRole('button', { name: /Excluir versão antiga/i })
    await user.click(deleteBtn)

    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível excluir essa versão.')
    expect(screen.queryByText('Não foi possível consultar a instalação.')).toBeNull()
  })
})


