import { useCallback, useState, type FormEvent } from 'react'
import { changePermissionRule, getEffectiveSettings, type RuleEntry, type RuleKind, type WritableLayer } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, FolderBanner, LAYER_LABEL, LAYER_SHORT, useFolderConversation, useLoaded, useSettingsClient } from './shared'

const KINDS: Array<{ kind: RuleKind; title: string; hint: string }> = [
  { kind: 'deny', title: 'Negar', hint: 'Nunca permitido. Vence qualquer regra de permitir.' },
  { kind: 'ask', title: 'Perguntar', hint: 'Sempre pede a sua confirmação.' },
  { kind: 'allow', title: 'Permitir', hint: 'Roda sem perguntar.' },
  { kind: 'additionalDirectories', title: 'Pastas adicionais', hint: 'Pastas além da da sessão que o agente pode usar.' },
]
const WRITABLE: readonly WritableLayer[] = ['user', 'project', 'local']

function isWritable(layer: string): layer is WritableLayer {
  return (WRITABLE as readonly string[]).includes(layer)
}

/**
 * `/permissions` (W7): every permission rule in force, with the layer and file it comes from (provenance). Rules of a
 * writable layer can be removed and added; the managed policy and the command line are read-only here.
 */
export function PermissionsSection() {
  const client = useSettingsClient()
  const conversationId = useFolderConversation()
  const load = useCallback(() => getEffectiveSettings(client, conversationId), [client, conversationId])
  const settings = useLoaded(load, 'ler as permissões')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<ReturnType<typeof describeError> | null>(null)
  const [draft, setDraft] = useState<{ kind: RuleKind; layer: WritableLayer; rule: string }>({ kind: 'allow', layer: 'user', rule: '' })

  const writable = settings.data ? WRITABLE.filter((layer) => settings.data?.writable[layer]) : []
  const layer = writable.includes(draft.layer) ? draft.layer : (writable[0] ?? 'user')

  async function change(kind: RuleKind, entry: { rule: string; layer: WritableLayer }, op: 'add' | 'remove') {
    setBusy(true)
    setActionError(null)
    try {
      await changePermissionRule(client, { layer: entry.layer, kind, rule: entry.rule, op, conversationId })
      if (op === 'add') setDraft((current) => ({ ...current, rule: '' }))
      settings.reload()
    } catch (error) {
      setActionError(describeError(error, op === 'add' ? 'adicionar a regra' : 'remover a regra'))
    } finally {
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const rule = draft.rule.trim()
    if (!rule || busy) return
    void change(draft.kind, { rule, layer }, 'add')
  }

  const data = settings.data
  return (
    <SettingsSection eyebrow="ORIN CODE / PERMISSÕES">
      {!settings.error?.localOnly && <FolderBanner projectRoot={data?.projectRoot ?? null} trusted={data?.trusted} conversationId={conversationId} />}
      <ErrorNote error={settings.error} />
      {settings.loading && <p className="code-settings__muted">Carregando…</p>}
      {data && data.errors.length > 0 && (
        <div className="code-settings__error" role="alert">
          <p>Arquivos de configuração com erro (o Orin usa a última versão válida):</p>
          <ul>{data.errors.map((error, index) => <li key={index}><code>{error.file}</code>: {error.message}</li>)}</ul>
        </div>
      )}
      {data && data.untrustedIgnored.length > 0 && (
        <p className="code-settings__warning">Ignorado até a pasta ser confiável: {data.untrustedIgnored.join(', ')}.</p>
      )}
      {data && KINDS.map(({ kind, title, hint }) => (
        <Card key={kind} title={title} id={`perm-${kind}`}>
          <p className="code-settings__muted">{hint}</p>
          {data.permissions[kind].length === 0 ? <p className="code-settings__empty">Nenhuma regra.</p> : (
            <ul className="code-settings__rules">
              {data.permissions[kind].map((entry: RuleEntry, index) => (
                <li key={`${entry.layer}:${entry.rule}:${index}`} className="code-settings__rule">
                  <code className="code-settings__rule-text">{entry.rule}</code>
                  <span className={`code-settings__layer code-settings__layer--${entry.layer}`} title={entry.file ?? undefined}>{LAYER_SHORT[entry.layer]}</span>
                  {entry.duplicate && <span className="code-settings__muted">repetida</span>}
                  {kind !== 'deny' && kind !== 'additionalDirectories' && data.permissions.deny.some((denied) => denied.rule === entry.rule) && (
                    <span className="code-settings__warning">sem efeito: também está em Negar</span>
                  )}
                  {entry.file && <span className="code-settings__file">{entry.file}</span>}
                  {/* W7-R5: the rule can be removed only from the file the editor writes for its layer. */}
                  {isWritable(entry.layer) && entry.file !== null && data.writable[entry.layer] === entry.file ? (
                    <button type="button" className="ghost-button" disabled={busy} aria-label={`Remover ${entry.rule} (${LAYER_SHORT[entry.layer]})`}
                      onClick={() => void change(kind, { rule: entry.rule, layer: entry.layer as WritableLayer }, 'remove')}>Remover</button>
                  ) : (
                    <span className="code-settings__muted">
                      {entry.layer === 'managed' ? 'definida pela política gerenciada'
                        : entry.layer === 'cli' ? 'vem da linha de comando'
                          : 'para removê-la, edite o arquivo acima'}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      ))}
      {data && writable.length > 0 && (
        <Card title="Adicionar uma regra" id="perm-add">
          <form className="code-settings__form" onSubmit={submit}>
            <label>Tipo
              <select value={draft.kind} onChange={(event) => setDraft((current) => ({ ...current, kind: event.target.value as RuleKind }))}>
                {KINDS.map((item) => <option key={item.kind} value={item.kind}>{item.title}</option>)}
              </select>
            </label>
            <label>Onde salvar
              <select value={layer} onChange={(event) => setDraft((current) => ({ ...current, layer: event.target.value as WritableLayer }))}>
                {writable.map((item) => <option key={item} value={item}>{LAYER_LABEL[item]}</option>)}
              </select>
            </label>
            <label className="code-settings__grow">Regra
              <input value={draft.rule} maxLength={500} placeholder={draft.kind === 'additionalDirectories' ? '../outra-pasta' : 'Bash(npm test:*)'}
                onChange={(event) => setDraft((current) => ({ ...current, rule: event.target.value }))} />
            </label>
            <button type="submit" className="button button--primary" disabled={busy || !draft.rule.trim()}>Adicionar</button>
          </form>
          <p className="code-settings__muted">Formato do Claude Code: <code>Ferramenta</code> ou <code>Ferramenta(especificador)</code>, como <code>Read(./src/**)</code> ou <code>WebFetch(domain:example.com)</code>.</p>
          <ErrorNote error={actionError} />
        </Card>
      )}
      {data && writable.length === 0 && !settings.error && (
        <p className="code-settings__muted">Nenhuma camada pode ser editada aqui.</p>
      )}
    </SettingsSection>
  )
}
