import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { LOCAL_CAPABILITIES } from '../../src/api/session'
import { SessionContext, type SessionContextValue } from '../../src/app/useSession'
import { ProfileMenu } from '../../src/features/auth/ProfileMenu'
import { WorkspaceFileCard } from '../../src/features/conversations/WorkspaceFileCard'
import { McpServerForm } from '../../src/features/mcp/McpServerForm'
import { PROVIDER_NAMES } from '../../src/api/providers'
import { ProviderGrid, type ProviderCardStates } from '../../src/features/providers/ProviderGrid'
import { visibleSettingsItems } from '../../src/features/settings/sections'

const SERVER_CAPS = { ...LOCAL_CAPABILITIES, shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

function server(isAdmin: boolean, signOut = vi.fn(async () => undefined)): SessionContextValue {
  return {
    mode: 'session', capabilities: SERVER_CAPS, isAdmin, refresh: async () => undefined, signOut,
    user: { userId: 'u', username: 'bruno', displayName: 'Bruno', role: isAdmin ? 'admin' : 'member', active: true, mustChangePassword: false },
  }
}

function withSession(session: SessionContextValue, node: ReactNode) {
  return render(<MemoryRouter><SessionContext.Provider value={session}>{node}</SessionContext.Provider></MemoryRouter>)
}

describe('capability-driven interface', () => {
  it('lists the profiles settings only for an admin of a server', () => {
    const ids = (session: { isAdmin: boolean; capabilities: typeof SERVER_CAPS }) => visibleSettingsItems(session).map((item) => item.id)
    expect(ids({ isAdmin: true, capabilities: SERVER_CAPS })).toContain('users')
    expect(ids({ isAdmin: false, capabilities: SERVER_CAPS })).not.toContain('users')
    expect(ids({ isAdmin: false, capabilities: LOCAL_CAPABILITIES })).not.toContain('users')
  })

  it('hides the open-in-desktop action and keeps download', () => {
    withSession(server(false), <WorkspaceFileCard reference={{ conversationId: 'c', path: 'a.md' }} client={new ApiClient()} />)
    expect(screen.queryByRole('button', { name: 'Abrir a.md no sistema' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Baixar a.md' })).toBeInTheDocument()
  })

  it('hides OmniRoute without the capability', () => {
    const states = Object.fromEntries(PROVIDER_NAMES.map((provider) => [provider, { status: 'unconfigured', detail: '' }])) as ProviderCardStates
    withSession(server(false), <ProviderGrid states={states} />)
    expect(screen.queryByText(/OmniRoute/i)).not.toBeInTheDocument()
  })

  it('offers only http transport for MCP without stdio', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    withSession(server(false), <McpServerForm client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onCreated={() => undefined} onClose={() => undefined} />)
    await userEvent.click(await screen.findByRole('button', { name: /manual/i }))
    expect(screen.queryByRole('option', { name: 'stdio' })).not.toBeInTheDocument()
  })

  it('shows the profile menu with sign-out only in session mode', async () => {
    const signOut = vi.fn(async () => undefined)
    withSession(server(false, signOut), <ProfileMenu />)
    await userEvent.click(screen.getByRole('button', { name: 'Perfil: Bruno' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sair' }))
    expect(signOut).toHaveBeenCalledOnce()
  })

  it('renders no profile menu locally', () => {
    const { container } = render(<MemoryRouter><ProfileMenu /></MemoryRouter>)
    expect(container).toBeEmptyDOMElement()
  })
})
