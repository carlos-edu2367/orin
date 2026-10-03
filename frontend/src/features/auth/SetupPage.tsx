import { useState, type FormEvent } from 'react'
import { setupInstance, type SessionState } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { AuthLayout } from './AuthLayout'
import { authMessage } from './authErrors'

export function SetupPage({ client, onReady }: { client: ApiClient; onReady: (session: SessionState) => void }) {
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (password !== confirmation) { setError('As senhas não conferem.'); return }
    setBusy(true); setError(null)
    try {
      onReady(await setupInstance(client, { token: token.trim(), username, password }))
    } catch (failure) {
      setError(authMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Criar o primeiro admin" lede="O token de setup aparece no log do servidor quando o Orin sobe pela primeira vez.">
      <form className="auth-form" onSubmit={(event) => void submit(event)}>
        <label>Token de setup<input name="token" autoComplete="off" autoCapitalize="none" spellCheck={false} value={token} onChange={(event) => setToken(event.target.value)} required /></label>
        <label>Usuário<input name="username" autoComplete="username" autoCapitalize="none" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Senha<input name="password" type="password" autoComplete="new-password" minLength={10} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        <label>Confirmar senha<input name="confirmation" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
        {error && <p className="auth-form__error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Criando…' : 'Criar admin'}</button>
      </form>
    </AuthLayout>
  )
}
