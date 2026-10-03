import { useState, type FormEvent } from 'react'
import { login, type SessionState } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { AuthLayout } from './AuthLayout'
import { authMessage } from './authErrors'

export function LoginPage({ client, onSignedIn, onSetupRequired }: { client: ApiClient; onSignedIn: (session: SessionState) => void; onSetupRequired: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError(null)
    try {
      onSignedIn(await login(client, { username, password }))
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'setup_required') { onSetupRequired(); return }
      setError(authMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Entrar">
      <form className="auth-form" onSubmit={(event) => void submit(event)}>
        <label>Usuário<input name="username" autoComplete="username" autoCapitalize="none" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Senha<input name="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        {error && <p className="auth-form__error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Entrando…' : 'Entrar'}</button>
      </form>
    </AuthLayout>
  )
}
