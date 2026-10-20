import { useCallback, useState } from 'react'
import { chooseOutputStyle, getOutputStyles } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, FolderBanner, useFolderConversation, useLoaded, useSettingsClient } from './shared'

const SOURCE: Record<string, string> = { builtin: 'do Orin', user: 'usuário', project: 'projeto', plugin: 'plugin' }

/**
 * `/output-style` (W7): the response styles (Orin's, the person's and the project's files) and which one is in use.
 * The choice is saved for the person, or — from a conversation — for this folder only (the local layer).
 */
export function OutputStylesSection() {
  const client = useSettingsClient()
  const conversationId = useFolderConversation()
  const load = useCallback(() => getOutputStyles(client, conversationId), [client, conversationId])
  const styles = useLoaded(load, 'ler os estilos de resposta')
  const [layer, setLayer] = useState<'user' | 'local'>(conversationId ? 'local' : 'user')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)

  async function choose(style: string) {
    setBusy(true)
    setError(null)
    try {
      await chooseOutputStyle(client, layer, style, conversationId)
      styles.reload()
    } catch (caught) {
      setError(describeError(caught, 'mudar o estilo'))
    } finally {
      setBusy(false)
    }
  }

  const data = styles.data
  return (
    <SettingsSection eyebrow="ORIN CODE / ESTILO DE RESPOSTA">
      {!styles.error?.localOnly && <FolderBanner projectRoot={null} conversationId={conversationId} />}
      <ErrorNote error={styles.error} />
      {styles.loading && !data && <p className="code-settings__muted">Carregando…</p>}
      {data && (
        <Card title="Estilos" id="styles-list">
          {conversationId && (
            <fieldset className="code-settings__scope">
              <legend>Salvar a escolha para</legend>
              <label><input type="radio" name="style-layer" checked={layer === 'local'} onChange={() => setLayer('local')} /> esta pasta (só você)</label>
              <label><input type="radio" name="style-layer" checked={layer === 'user'} onChange={() => setLayer('user')} /> todas as pastas</label>
            </fieldset>
          )}
          <ul className="code-settings__styles" aria-label="Estilos de resposta">
            {data.styles.map((style) => {
              const active = style.key === data.active
              return (
                <li key={style.key} className={`code-settings__style${active ? ' is-active' : ''}`}>
                  <div>
                    <strong>{style.name || style.key}</strong>
                    <span className="code-settings__layer">{SOURCE[style.source] ?? style.source}</span>
                    {active && <span className="code-settings__active"> · em uso{data.activeLayer ? ` (${data.activeLayer})` : ''}</span>}
                    {style.description && <p className="code-settings__muted">{style.description}</p>}
                  </div>
                  {!active && <button type="button" className="ghost-button" disabled={busy} onClick={() => void choose(style.key)}>Usar</button>}
                </li>
              )
            })}
          </ul>
          <ErrorNote error={error} />
        </Card>
      )}
    </SettingsSection>
  )
}
