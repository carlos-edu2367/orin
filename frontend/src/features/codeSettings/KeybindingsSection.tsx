import { useCallback, useState } from 'react'
import { getKeybindingsFiles, saveKeybindings, type KeybindingsFiles } from '../../api/settings'
import { validateKeybindings } from '../keybindings/bindings'
import { useKeybindings } from '../keybindings/engine'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, useLoaded, useSettingsClient } from './shared'

export const KEYBINDINGS_TEMPLATE = `{
  "bindings": [
    { "context": "Chat", "bindings": { "ctrl+enter": "chat:submit" } }
  ]
}
`

function Editor({ files, onSaved }: { files: KeybindingsFiles; onSaved: () => void }) {
  const client = useSettingsClient()
  const { environment, reload } = useKeybindings()
  // W7-R4: the baseline is the text last saved (the PUT answer), so Save is disabled until the text changes again.
  const [baseline, setBaseline] = useState(files.orin.text ?? '')
  const [text, setText] = useState(baseline || KEYBINDINGS_TEMPLATE)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)
  const [saved, setSaved] = useState(false)
  const check = validateKeybindings(text, environment)

  async function save() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const next = await saveKeybindings(client, text)
      const savedText = next.orin.text ?? text
      setBaseline(savedText)
      setText(savedText)
      reload() // the new keys apply at once
      setSaved(true)
      onSaved()
    } catch (caught) {
      setError(describeError(caught, 'salvar os atalhos'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Card title="Atalhos do Orin" id="keys-orin">
        <p className="code-settings__file">{files.orin.path}</p>
        {files.orin.error && <p className="code-settings__warning">O arquivo atual tem um erro: {files.orin.error}</p>}
        <label htmlFor="keys-text" className="code-settings__label">keybindings.json</label>
        <textarea id="keys-text" className="code-settings__code" spellCheck={false} rows={14} value={text}
          onChange={(event) => { setText(event.target.value); setSaved(false) }} />
        {check.errors.length > 0 && (
          <ul className="code-settings__problems" role="status" aria-label="Erros">{check.errors.map((item, index) => <li key={index}>{item}</li>)}</ul>
        )}
        {check.warnings.length > 0 && (
          <ul className="code-settings__warnings" aria-label="Avisos">{check.warnings.map((item, index) => <li key={index}>{item}</li>)}</ul>
        )}
        <div className="code-settings__actions">
          <button type="button" className="button button--primary" disabled={busy || check.errors.length > 0 || text === baseline} onClick={() => void save()}>
            {busy ? 'Salvando…' : 'Salvar'}
          </button>
          {saved && <span className="code-settings__muted" role="status">Salvo; os atalhos já valem.</span>}
        </div>
        <ErrorNote error={error} />
      </Card>
      <Card title="Atalhos do Claude Code (somente leitura)" id="keys-claude">
        <p className="code-settings__file">{files.claude.path}</p>
        <p className="code-settings__muted">O Orin também lê este arquivo; os atalhos do Orin vencem quando os dois definem a mesma tecla.</p>
        {files.claude.text ? <pre className="code-settings__code" tabIndex={0}>{files.claude.text}</pre> : <p className="code-settings__empty">Arquivo não encontrado.</p>}
      </Card>
    </>
  )
}

/** `/keybindings` (W7): the person's keybindings file, checked as they type with the same rules the interface uses. */
export function KeybindingsSection() {
  const client = useSettingsClient()
  const load = useCallback(() => getKeybindingsFiles(client), [client])
  const files = useLoaded(load, 'ler os atalhos')
  return (
    <SettingsSection eyebrow="ORIN CODE / ATALHOS">
      <ErrorNote error={files.error} />
      {files.loading && !files.data && <p className="code-settings__muted">Carregando…</p>}
      {files.data && <Editor files={files.data} onSaved={files.reload} />}
    </SettingsSection>
  )
}
