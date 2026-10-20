import { useCallback, useState } from 'react'
import { getHooks, saveHooks, type HooksReport, type WritableLayer } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, FolderBanner, LAYER_LABEL, useFolderConversation, useLoaded, useSettingsClient } from './shared'

const LAYERS: readonly WritableLayer[] = ['user', 'project', 'local']
export const HOOKS_TEXT_MAX = 200_000

/** The JSON of a layer's `hooks` object as the person edits it; `null` when it is not an object of event lists. */
export function parseHooksText(text: string): { hooks: Record<string, unknown> | null; error: string | null } {
  if (text.length > HOOKS_TEXT_MAX) return { hooks: null, error: 'O texto é grande demais.' }
  let value: unknown
  try {
    value = text.trim() ? JSON.parse(text) : {}
  } catch (error) {
    return { hooks: null, error: `JSON inválido: ${error instanceof Error ? error.message : 'erro de sintaxe'}` }
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return { hooks: null, error: 'Os hooks são um objeto: { "Evento": [ ... ] }.' }
  const wrong = Object.entries(value).filter(([, entries]) => !Array.isArray(entries)).map(([event]) => event)
  if (wrong.length > 0) return { hooks: null, error: `Cada evento tem uma lista de grupos; não é uma lista: ${wrong.join(', ')}.` }
  return { hooks: value as Record<string, unknown>, error: null }
}

function LayerEditor({ layer, entry, onSave }: {
  layer: WritableLayer
  entry: NonNullable<HooksReport['editable'][WritableLayer]>
  onSave: (layer: WritableLayer, hooks: Record<string, unknown>) => Promise<ReturnType<typeof describeError> | null>
}) {
  const initial = JSON.stringify(entry.hooks, null, 2)
  const [text, setText] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)
  const [saved, setSaved] = useState(false)
  const parsed = parseHooksText(text)

  async function save() {
    if (!parsed.hooks) return
    setBusy(true)
    setSaved(false)
    const failure = await onSave(layer, parsed.hooks)
    setError(failure)
    setSaved(failure === null)
    setBusy(false)
  }

  const id = `hooks-${layer}`
  return (
    <Card title={LAYER_LABEL[layer]} id={`${id}-title`}>
      <p className="code-settings__file">{entry.file}</p>
      <label htmlFor={id} className="code-settings__label">Hooks deste arquivo (JSON)</label>
      <textarea id={id} className="code-settings__code" spellCheck={false} rows={10} value={text}
        onChange={(event) => { setText(event.target.value); setSaved(false) }} />
      {parsed.error && <p className="code-settings__warning" role="status">{parsed.error}</p>}
      <div className="code-settings__actions">
        <button type="button" className="button button--primary" disabled={busy || !parsed.hooks || text === initial} onClick={() => void save()}>
          {busy ? 'Salvando…' : 'Salvar'}
        </button>
        {saved && <span className="code-settings__muted" role="status">Salvo.</span>}
      </div>
      <ErrorNote error={error} />
    </Card>
  )
}

/**
 * `/hooks` (W7): the hooks in force (from every layer, plugins, skills and agents) and an editor for the `hooks` of
 * each settings file the person can write. The server validates what is saved; a hook of the project's file runs
 * only once the folder is trusted.
 */
export function HooksSection() {
  const client = useSettingsClient()
  const conversationId = useFolderConversation()
  const load = useCallback(() => getHooks(client, conversationId), [client, conversationId])
  const hooks = useLoaded(load, 'ler os hooks')
  const [generation, setGeneration] = useState(0)

  async function save(layer: WritableLayer, value: Record<string, unknown>) {
    try {
      await saveHooks(client, layer, value, conversationId)
      hooks.reload()
      setGeneration((current) => current + 1)
      return null
    } catch (error) {
      return describeError(error, 'salvar os hooks')
    }
  }

  const data = hooks.data
  return (
    <SettingsSection eyebrow="ORIN CODE / HOOKS">
      {!hooks.error?.localOnly && <FolderBanner projectRoot={null} conversationId={conversationId} />}
      <ErrorNote error={hooks.error} />
      {hooks.loading && !data && <p className="code-settings__muted">Carregando…</p>}
      {data?.disabled && <p className="code-settings__warning" role="status">Os hooks estão desligados (<code>disableAllHooks</code>): nada abaixo roda.</p>}
      {data && data.errors.length > 0 && (
        <div className="code-settings__error" role="alert">
          <p>Hooks com erro (ignorados):</p>
          <ul>{data.errors.map((error, index) => <li key={index}>{error.source}: {error.message}</li>)}</ul>
        </div>
      )}
      {data && (
        <Card title="Em vigor" id="hooks-effective">
          {data.effective.length === 0 ? <p className="code-settings__empty">Nenhum hook.</p> : (
            <div className="code-settings__table-wrap" role="region" aria-label="Hooks em vigor" tabIndex={0}>
              <table className="code-settings__table">
                <thead><tr><th scope="col">Evento</th><th scope="col">Matcher</th><th scope="col">Tipo</th><th scope="col">Origem</th><th scope="col">O quê</th><th scope="col">Tempo-limite</th></tr></thead>
                <tbody>
                  {data.effective.map((hook, index) => (
                    <tr key={index}>
                      <td>{hook.event}</td><td><code>{hook.matcher || '*'}</code></td><td>{hook.type}</td><td>{hook.source}</td>
                      <td><code>{hook.label}</code></td><td>{hook.timeout ? `${hook.timeout} s` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      {data && LAYERS.map((layer) => {
        const entry = data.editable[layer]
        return entry ? <LayerEditor key={`${layer}:${generation}`} layer={layer} entry={entry} onSave={save} /> : null
      })}
    </SettingsSection>
  )
}
