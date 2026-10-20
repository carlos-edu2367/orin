import { useCallback, useState } from 'react'
import { getTrustedFolders, revokeTrustedFolder } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, useLoaded, useSettingsClient } from './shared'

/**
 * The folders the person trusted (W7). A trusted folder's own settings, hooks, MCP servers, skills and agents load;
 * revoking turns them off again until the folder is trusted anew (from a conversation in it).
 */
export function TrustedFoldersSection() {
  const client = useSettingsClient()
  const load = useCallback(() => getTrustedFolders(client), [client])
  const folders = useLoaded(load, 'ler as pastas confiáveis')
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)

  async function revoke(path: string) {
    setBusy(true)
    setError(null)
    try {
      await revokeTrustedFolder(client, path)
      setConfirming(null)
      folders.reload()
    } catch (caught) {
      setError(describeError(caught, 'revogar a confiança'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsSection eyebrow="ORIN CODE / PASTAS CONFIÁVEIS">
      <ErrorNote error={folders.error} />
      {folders.loading && !folders.data && <p className="code-settings__muted">Carregando…</p>}
      {folders.data && (
        <Card title="Pastas" id="trusted-list">
          <p className="code-settings__muted">Numa pasta confiável, as configurações, hooks, servidores MCP, skills e agentes do projeto são carregados.</p>
          {folders.data.length === 0 ? <p className="code-settings__empty">Nenhuma pasta confiável.</p> : (
            <ul className="code-settings__rules">
              {folders.data.map((path) => (
                <li key={path} className="code-settings__rule">
                  <code className="code-settings__rule-text">{path}</code>
                  {confirming === path ? (
                    <>
                      <button type="button" className="button button--danger" disabled={busy} onClick={() => void revoke(path)}>Revogar</button>
                      <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</button>
                    </>
                  ) : (
                    <button type="button" className="ghost-button" disabled={busy} aria-label={`Revogar a confiança em ${path}`} onClick={() => setConfirming(path)}>Revogar…</button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <ErrorNote error={error} />
        </Card>
      )}
    </SettingsSection>
  )
}
