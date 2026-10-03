import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { getSessionCsrf, notifyUnauthenticated, setSessionCsrf } from '../../src/api/session'
import { SessionGate } from '../../src/app/SessionGate'
import { useSession } from '../../src/app/useSession'

const USER = { user_id: 'local-user', username: 'carla', display_name: 'Carla', role: 'admin', active: true, must_change_password: false, created_at: 'x', updated_at: 'x', password_changed_at: 'x' }
const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
function apiError(code: string, status: number, retryAfter: number | null = null): Response {
  return json({ error: { code, category: 'X', message_key: code, correlation_id: 'c', retryable: false, retry_after: retryAfter } }, status)
}

function Home() {
  const session = useSession()
  return <p>app de {session.user?.displayName}</p>
}

function renderGate(fetchImpl: typeof fetch, path = '/') {
  const client = new ApiClient({ fetchImpl, maxAttempts: 1, csrfToken: getSessionCsrf })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionGate client={client}>
        <Routes><Route path="*" element={<Home />} /></Routes>
      </SessionGate>
    </MemoryRouter>,
  )
}

beforeEach(() => { document.head.innerHTML = '<meta name="agentos-auth-mode" content="session">' })
afterEach(() => { document.head.innerHTML = ''; setSessionCsrf(undefined) })

describe('SessionGate', () => {
  it('renders the app directly in local mode', async () => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
    const fetchImpl = vi.fn<typeof fetch>()
    renderGate(fetchImpl)
    expect(await screen.findByText(/app de/)).toBeInTheDocument()
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('enters the app with a valid session and keeps the csrf token', async () => {
    renderGate(vi.fn<typeof fetch>(() => Promise.resolve(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
    expect(getSessionCsrf()).toBe('tok')
  })

  it('signs in from the login screen', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(apiError('authentication_required', 401))
      .mockResolvedValueOnce(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'a long password')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
  })

  it('shows a generic error and the lock time', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(apiError('authentication_required', 401))
      .mockResolvedValueOnce(apiError('invalid_credentials', 401))
      .mockResolvedValueOnce(apiError('login_locked', 429, 600))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'wrong password')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Usuário ou senha incorretos.')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Tente de novo em 10 min.')
  })

  it('asks for the setup token on a fresh instance', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(apiError('setup_required', 409))
      .mockResolvedValueOnce(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }, 201))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Token de setup'), 'the-token')
    await userEvent.type(screen.getByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'a long password')
    await userEvent.type(screen.getByLabelText('Confirmar senha'), 'a long password')
    await userEvent.click(screen.getByRole('button', { name: 'Criar admin' }))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
    expect(JSON.parse(String(fetchImpl.mock.calls[1][1]?.body))).toEqual({ token: 'the-token', username: 'carla', password: 'a long password' })
  })

  it('refuses mismatched setup passwords without calling the api', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(apiError('setup_required', 409))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Token de setup'), 't')
    await userEvent.type(screen.getByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'a long password')
    await userEvent.type(screen.getByLabelText('Confirmar senha'), 'another password')
    await userEvent.click(screen.getByRole('button', { name: 'Criar admin' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('As senhas não conferem.')
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('forces a temporary password to be changed first', async () => {
    const pending = { ...USER, must_change_password: true }
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ user: pending, csrf_token: 'tok', capabilities: CAPS }))
      .mockResolvedValueOnce(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Senha atual'), 'Tmp-123456789012')
    await userEvent.type(screen.getByLabelText('Nova senha'), 'my own password')
    await userEvent.type(screen.getByLabelText('Confirmar nova senha'), 'my own password')
    await userEvent.click(screen.getByRole('button', { name: 'Trocar senha' }))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
  })

  it('goes back to the login screen when a request reports 401', async () => {
    renderGate(vi.fn<typeof fetch>(() => Promise.resolve(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))))
    await screen.findByText('app de Carla')
    notifyUnauthenticated()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Entrar' })).toBeInTheDocument())
    expect(getSessionCsrf()).toBeUndefined()
  })
})
