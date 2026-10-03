import { useState, type FormEvent } from 'react'
import { changePassword, type SessionState, type SessionUser } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { AuthLayout } from './AuthLayout'
import { authMessage } from './authErrors'

export function ChangePasswordPage({ client, user, required, onChanged, onCancel, onSignOut }: {
  client: ApiClient
  user: SessionUser
  required: boolean
  onChanged: (session: SessionState) => void
  onCancel?: () => void
  onSignOut: () => void
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (next !== confirmation) { setError('As senhas não conferem.'); return }
    setBusy(true); setError(null)
    try {
      onChanged(await changePassword(client, { currentPassword: current, newPassword: next }))
    } catch (failure) {
      setError(authMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Trocar senha" lede={required ? `Olá, ${user.displayName}. Defina sua própria senha antes de continuar.` : undefined}>
      <form className="auth-form" onSubmit={(event) => void submit(event)}>
        <input type="text" name="username" autoComplete="username" value={user.username} readOnly hidden />
        <label>Senha atual<input name="current" type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required /></label>
        <label>Nova senha<input name="new" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(event) => setNext(event.target.value)} required /></label>
        <label>Confirmar nova senha<input name="confirmation" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
        {error && <p className="auth-form__error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Salvando…' : 'Trocar senha'}</button>
        {required
          ? <button type="button" className="button button--quiet" onClick={onSignOut}>Sair</button>
          : <button type="button" className="button button--quiet" onClick={onCancel}>Cancelar</button>}
      </form>
    </AuthLayout>
  )
}
