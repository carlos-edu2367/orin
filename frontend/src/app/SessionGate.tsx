import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { getMe, logout, type SessionState } from '../api/auth'
import { readBrowserSessionBootstrap } from '../api/browserSession'
import { createBrowserApiClient, type ApiClient } from '../api/client'
import { ApiError } from '../api/errors'
import { onUnauthenticated, setSessionCsrf } from '../api/session'
import { ChangePasswordPage } from '../features/auth/ChangePasswordPage'
import { LoginPage } from '../features/auth/LoginPage'
import { SetupPage } from '../features/auth/SetupPage'
import { SessionContext, type SessionContextValue } from './useSession'

type GateState =
  | { status: 'loading' }
  | { status: 'anonymous' }
  | { status: 'setup' }
  | { status: 'error' }
  | { status: 'ready'; session: SessionState }

const AUTH_PATHS = new Set(['/login', '/setup', '/change-password'])

export function SessionGate({ children, client: providedClient }: { children: ReactNode; client?: ApiClient }) {
  const sessionMode = typeof document !== 'undefined' && readBrowserSessionBootstrap(document).status === 'session'
  if (!sessionMode) return <>{children}</>
  return <ServerSessionGate client={providedClient}>{children}</ServerSessionGate>
}

function ServerSessionGate({ children, client: providedClient }: { children: ReactNode; client?: ApiClient }) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const navigate = useNavigate()
  const location = useLocation()
  const [state, setState] = useState<GateState>({ status: 'loading' })

  const apply = useCallback((next: GateState) => {
    setSessionCsrf(next.status === 'ready' ? next.session.csrfToken ?? undefined : undefined)
    setState(next)
  }, [])
  const accept = useCallback((session: SessionState) => apply({ status: 'ready', session }), [apply])

  const resolve = useCallback(async (): Promise<GateState> => {
    try {
      return { status: 'ready', session: await getMe(client) }
    } catch (error) {
      if (error instanceof ApiError && error.code === 'setup_required') return { status: 'setup' }
      if (error instanceof ApiError && error.status === 401) return { status: 'anonymous' }
      return { status: 'error' }
    }
  }, [client])

  const load = useCallback(async () => apply(await resolve()), [apply, resolve])

  useEffect(() => {
    let active = true
    void resolve().then((next) => { if (active) apply(next) })
    return () => { active = false }
  }, [apply, resolve])
  useEffect(() => onUnauthenticated(() => { setSessionCsrf(undefined); setState({ status: 'anonymous' }) }), [])

  const nextPath = new URLSearchParams(location.search).get('next')
  useEffect(() => {
    const here = location.pathname
    if (state.status === 'anonymous' && here !== '/login') navigate(`/login?next=${encodeURIComponent(here + location.search)}`, { replace: true })
    if (state.status === 'setup' && here !== '/setup') navigate('/setup', { replace: true })
    if (state.status === 'ready' && state.session.user.mustChangePassword && here !== '/change-password') navigate('/change-password', { replace: true })
    if (state.status === 'ready' && !state.session.user.mustChangePassword && (here === '/login' || here === '/setup')) {
      navigate(nextPath && nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/', { replace: true })
    }
  }, [state, location.pathname, location.search, navigate, nextPath])

  const signOut = useCallback(async () => {
    try { await logout(client) } catch { /* the session may already be gone */ }
    setSessionCsrf(undefined)
    setState({ status: 'anonymous' })
  }, [client])

  if (state.status === 'loading') return <main className="auth-page" aria-busy="true"><p className="auth-card__lede">Carregando…</p></main>
  if (state.status === 'error') return <main className="auth-page"><p role="alert">Não foi possível falar com o servidor.</p><button type="button" className="button button--quiet" onClick={() => void load()}>Tentar de novo</button></main>
  if (state.status === 'setup') return <SetupPage client={client} onReady={accept} />
  if (state.status === 'anonymous') return <LoginPage client={client} onSignedIn={accept} onSetupRequired={() => setState({ status: 'setup' })} />

  const { session } = state
  if (session.user.mustChangePassword || location.pathname === '/change-password') {
    return <ChangePasswordPage
      client={client} user={session.user} required={session.user.mustChangePassword}
      onChanged={(updated) => { accept(updated); navigate('/', { replace: true }) }}
      onCancel={() => navigate(-1)} onSignOut={() => void signOut()}
    />
  }
  const value: SessionContextValue = {
    mode: 'session', user: session.user, capabilities: session.capabilities, isAdmin: session.user.role === 'admin',
    refresh: load, signOut,
  }
  return <SessionContext.Provider value={value}>{AUTH_PATHS.has(location.pathname) ? null : children}</SessionContext.Provider>
}
