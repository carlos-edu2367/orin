import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { createUser, listUsers, resetUserPassword, updateUser, type SessionUser } from '../../api/auth'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { useSession } from '../../app/useSession'
import { authMessage } from '../auth/authErrors'
import { SettingsSection } from '../settings/SettingsSection'

type Revealed = { username: string; password: string }

export function UsersSection({ client: providedClient }: { client?: ApiClient }) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const session = useSession()
  const [users, setUsers] = useState<SessionUser[]>([])
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState<'admin' | 'member'>('member')
  const [revealed, setRevealed] = useState<Revealed | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try { setUsers(await listUsers(client)) } catch (failure) { setError(authMessage(failure)) }
  }, [client])

  useEffect(() => {
    let active = true
    listUsers(client)
      .then((items) => { if (active) setUsers(items) })
      .catch((failure: unknown) => { if (active) setError(authMessage(failure)) })
    return () => { active = false }
  }, [client])

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await action(); await reload() } catch (failure) { setError(authMessage(failure)) } finally { setBusy(false) }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      const created = await createUser(client, { username, displayName, role })
      setRevealed({ username: created.user.username, password: created.temporaryPassword })
      setUsername(''); setDisplayName(''); setRole('member')
    })
  }

  return (
    <SettingsSection eyebrow="SISTEMA / PERFIS">
      {revealed && (
        <div className="users-reveal" role="status">
          <p>Senha provisória de <strong>{revealed.username}</strong>: <code>{revealed.password}</code></p>
          <p>Ela não será mostrada de novo. Envie para a pessoa; ela troca no primeiro login.</p>
          <button type="button" className="button button--quiet" onClick={() => void navigator.clipboard?.writeText(revealed.password)}>Copiar</button>
          <button type="button" className="button button--quiet" onClick={() => setRevealed(null)}>Fechar</button>
        </div>
      )}
      {error && <p className="users-error" role="alert">{error}</p>}
      <table className="users-table">
        <thead><tr><th scope="col">Nome</th><th scope="col">Usuário</th><th scope="col">Papel</th><th scope="col">Estado</th><th scope="col"><span className="visually-hidden">Ações</span></th></tr></thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.userId}>
              <td>{user.displayName}</td>
              <td><code>{user.username}</code></td>
              <td>{user.role === 'admin' ? 'Admin' : 'Membro'}</td>
              <td>{user.active ? (user.mustChangePassword ? 'Aguardando troca de senha' : 'Ativo') : 'Desativado'}</td>
              <td className="users-table__actions">
                <button type="button" disabled={busy} onClick={() => void run(async () => { await updateUser(client, user.userId, { role: user.role === 'admin' ? 'member' : 'admin' }) })}>{user.role === 'admin' ? 'Tornar membro' : 'Tornar admin'}</button>
                {user.userId !== session.user?.userId && (
                  <button type="button" disabled={busy} onClick={() => void run(async () => { await updateUser(client, user.userId, { active: !user.active }) })}>{user.active ? 'Desativar' : 'Reativar'}</button>
                )}
                <button type="button" disabled={busy} onClick={() => void run(async () => {
                  const reset = await resetUserPassword(client, user.userId)
                  setRevealed({ username: reset.user.username, password: reset.temporaryPassword })
                })}>Resetar senha</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="users-create" onSubmit={submit}>
        <h2>Novo perfil</h2>
        <label>Usuário do novo perfil<input value={username} autoCapitalize="none" onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Nome de exibição<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
        <label>Papel<select value={role} onChange={(event) => setRole(event.target.value as 'admin' | 'member')}><option value="member">Membro</option><option value="admin">Admin</option></select></label>
        <button type="submit" className="button button--primary" disabled={busy}>Criar perfil</button>
      </form>
    </SettingsSection>
  )
}
