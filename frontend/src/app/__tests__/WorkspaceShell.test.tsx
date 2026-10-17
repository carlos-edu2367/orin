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

    // Sidebar hubs links exist and redundant "Seu espaço" heading next to logo is removed
    expect(screen.queryByRole('heading', { name: /Seu espaço/i })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Biblioteca/i })).toHaveAttribute('href', '/library/skills')
    expect(screen.getByRole('link', { name: /Integrações/i })).toHaveAttribute('href', '/integrations/providers')
    expect(screen.getByRole('link', { name: /Automações/i })).toHaveAttribute('href', '/schedules')

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
})
