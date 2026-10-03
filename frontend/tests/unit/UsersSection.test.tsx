import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { SessionContext } from '../../src/app/useSession'
import { UsersSection } from '../../src/features/users/UsersSection'

const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }
const ADMIN = { user_id: 'local-user', username: 'carla', display_name: 'Carla', role: 'admin', active: true, must_change_password: false, created_at: 'x', updated_at: 'x', password_changed_at: 'x' }
const MEMBER = { ...ADMIN, user_id: 'usr_1', username: 'bruno', display_name: 'Bruno', role: 'member' }

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function renderSection(fetchImpl: typeof fetch) {
  const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
  const session = { mode: 'session' as const, user: { userId: 'local-user', username: 'carla', displayName: 'Carla', role: 'admin' as const, active: true, mustChangePassword: false }, capabilities: CAPS, isAdmin: true, refresh: async () => undefined, signOut: async () => undefined }
  return render(<MemoryRouter initialEntries={['/settings/users']}><SessionContext.Provider value={session}><UsersSection client={client} /></SessionContext.Provider></MemoryRouter>)
}

describe('UsersSection', () => {
  it('lists profiles and shows a new temporary password once', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ items: [ADMIN] }))
      .mockResolvedValueOnce(json({ user: { ...MEMBER, must_change_password: true }, temporary_password: 'Tmp-123456789012' }, 201))
      .mockResolvedValueOnce(json({ items: [ADMIN, MEMBER] }))
    renderSection(fetchImpl)
    expect(await screen.findByText('Carla')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Usuário do novo perfil'), 'bruno')
    await userEvent.click(screen.getByRole('button', { name: 'Criar perfil' }))
    expect(await screen.findByText('Tmp-123456789012')).toBeInTheDocument()
    expect(screen.getByText(/Ela não será mostrada de novo/)).toBeInTheDocument()
    expect(await screen.findByText('Bruno')).toBeInTheDocument()
  })

  it('explains why the last admin cannot be demoted', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ items: [ADMIN] }))
      .mockResolvedValueOnce(json({ error: { code: 'last_admin', category: 'CONFLICT', message_key: 'last_admin', correlation_id: 'c', retryable: false, retry_after: null } }, 409))
    renderSection(fetchImpl)
    const row = (await screen.findByText('Carla')).closest('tr') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: 'Tornar membro' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A instância precisa de pelo menos um admin ativo.')
  })
})
