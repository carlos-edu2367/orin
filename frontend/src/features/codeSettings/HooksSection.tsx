import { useCallback, useState } from 'react'
import { getHooks, saveHooks, type HooksReport, type WritableLayer } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, FolderBanner, LAYER_LABEL, useFolderConversation, useLoaded, useSettingsClient } from './shared'

const LAYERS: readonly WritableLayer[] = ['user', 'project', 'local']
export const HOOKS_TEXT_MAX = 200_000
export const AGENT_HOOK_AGENTS = ['explore', 'verifier'] as const
export type AgentHookAgent = (typeof AGENT_HOOK_AGENTS)[number]
const HOOK_TYPES = ['command', 'http', 'prompt', 'agent'] as const
type EditableHookType = (typeof HOOK_TYPES)[number]
const HOOK_EVENTS = ['Stop', 'PreToolUse', 'PostToolUse', 'SubagentStop', 'UserPromptSubmit', 'SessionStart'] as const

type AgentHookEntryRef = {
  event: string
  groupIndex: number
  hookIndex: number
  prompt: string
  agent: AgentHookAgent
}

function normalizeAgentChoice(raw: unknown): AgentHookAgent {
  return typeof raw === 'string' && raw.trim().toLowerCase() === 'verifier' ? 'verifier' : 'explore'
}

export function listAgentHooks(hooks: Record<string, unknown>): AgentHookEntryRef[] {
  const found: AgentHookEntryRef[] = []
  for (const [event, groups] of Object.entries(hooks)) {
    if (!Array.isArray(groups)) continue
    groups.forEach((group, groupIndex) => {
      if (typeof group !== 'object' || group === null || Array.isArray(group)) return
      const entries = (group as Record<string, unknown>).hooks
      if (!Array.isArray(entries)) return
      entries.forEach((entry, hookIndex) => {
        if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return
        const rec = entry as Record<string, unknown>
        if (rec.type === 'agent') {
          found.push({
            event,
            groupIndex,
            hookIndex,
            prompt: typeof rec.prompt === 'string' ? rec.prompt : '',
            agent: normalizeAgentChoice(rec.agent),
          })
        }
      })
    })
  }
  return found
}

export function setAgentHookSelection(
  hooks: Record<string, unknown>,
  event: string,
  groupIndex: number,
  hookIndex: number,
  agent: AgentHookAgent,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...hooks }
  const groups = next[event]
  if (!Array.isArray(groups) || !groups[groupIndex]) return next
  const nextGroups = [...groups]
  const rawGroup = nextGroups[groupIndex]
  if (typeof rawGroup !== 'object' || rawGroup === null || Array.isArray(rawGroup)) return next
  const groupObj = { ...(rawGroup as Record<string, unknown>) }
  const entries = groupObj.hooks
  if (!Array.isArray(entries) || !entries[hookIndex]) return next
  const nextEntries = [...entries]
  const rawEntry = nextEntries[hookIndex]
  if (typeof rawEntry !== 'object' || rawEntry === null || Array.isArray(rawEntry)) return next
  nextEntries[hookIndex] = { ...(rawEntry as Record<string, unknown>), agent }
  groupObj.hooks = nextEntries
  nextGroups[groupIndex] = groupObj
  next[event] = nextGroups
  return next
}

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
  onSave: (layer: WritableLayer, hooks: Record<string, unknown>) => Promise<{ hooks: unknown } | { failure: ReturnType<typeof describeError> }>
}) {
  // W7-R4: the baseline is the text last saved (the PUT answer), not the text the editor mounted with.
  const [baseline, setBaseline] = useState(() => JSON.stringify(entry.hooks, null, 2))
  const [text, setText] = useState(baseline)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)
  const [saved, setSaved] = useState(false)
  const [newEvent, setNewEvent] = useState<string>(HOOK_EVENTS[0])
  const [newType, setNewType] = useState<EditableHookType>('command')
  const [newAgent, setNewAgent] = useState<AgentHookAgent>('explore')
  const [newValue, setNewValue] = useState('')
  const parsed = parseHooksText(text)
  const agentHooks = parsed.hooks ? listAgentHooks(parsed.hooks) : []

  function updateAgentChoice(item: AgentHookEntryRef, agent: AgentHookAgent) {
    if (!parsed.hooks) return
    const next = setAgentHookSelection(parsed.hooks, item.event, item.groupIndex, item.hookIndex, agent)
    setText(JSON.stringify(next, null, 2))
    setSaved(false)
  }

  function addHookEntry() {
    if (!parsed.hooks || !newValue.trim()) return
    const next: Record<string, unknown> = { ...parsed.hooks }
    const existing = Array.isArray(next[newEvent]) ? [...(next[newEvent] as unknown[])] : []
    const spec: Record<string, unknown> =
      newType === 'command'
        ? { type: 'command', command: newValue.trim() }
        : newType === 'http'
          ? { type: 'http', url: newValue.trim() }
          : newType === 'prompt'
            ? { type: 'prompt', prompt: newValue.trim() }
            : { type: 'agent', prompt: newValue.trim(), agent: newAgent }
    existing.push({ matcher: '', hooks: [spec] })
    next[newEvent] = existing
    setText(JSON.stringify(next, null, 2))
    setNewValue('')
    setSaved(false)
  }

  async function save() {
    if (!parsed.hooks) return
    setBusy(true)
    setSaved(false)
    const result = await onSave(layer, parsed.hooks)
    if ('failure' in result) {
      setError(result.failure)
    } else {
      const next = JSON.stringify(result.hooks ?? parsed.hooks, null, 2)
      setBaseline(next)
      setText(next)
      setError(null)
      setSaved(true)
    }
    setBusy(false)
  }

  const id = `hooks-${layer}`
  const valuePlaceholder =
    newType === 'command' ? 'Comando shell' : newType === 'http' ? 'https://...' : 'Instrução do prompt'
  return (
    <Card title={LAYER_LABEL[layer]} id={`${id}-title`}>
      <p className="code-settings__file">{entry.file}</p>
      {agentHooks.length > 0 && (
        <div className="code-settings__agent-hooks">
          <p className="code-settings__label">Hooks do tipo agente</p>
          {agentHooks.map((item) => {
            const selectId = `${id}-agent-${item.event}-${item.groupIndex}-${item.hookIndex}`
            return (
              <div key={selectId} className="code-settings__row">
                <span>
                  <strong>{item.event}</strong>
                  {item.prompt ? ` — ${item.prompt.slice(0, 60)}` : ''}
                </span>
                <label htmlFor={selectId} className="code-settings__label">Agente</label>
                <select
                  id={selectId}
                  value={item.agent}
                  onChange={(event) => updateAgentChoice(item, normalizeAgentChoice(event.target.value))}
                >
                  {AGENT_HOOK_AGENTS.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </div>
            )
          })}
        </div>
      )}
      <div className="code-settings__row">
        <label htmlFor={`${id}-new-event`} className="code-settings__label">Evento</label>
        <select id={`${id}-new-event`} value={newEvent} onChange={(event) => setNewEvent(event.target.value)}>
          {HOOK_EVENTS.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
        </select>
        <label htmlFor={`${id}-new-type`} className="code-settings__label">Tipo</label>
        <select
          id={`${id}-new-type`}
          value={newType}
          onChange={(event) => setNewType(event.target.value as EditableHookType)}
        >
          {HOOK_TYPES.map((kind) => <option key={kind} value={kind}>{kind}</option>)}
        </select>
        {newType === 'agent' && (
          <>
            <label htmlFor={`${id}-new-agent`} className="code-settings__label">Agente</label>
            <select
              id={`${id}-new-agent`}
              value={newAgent}
              onChange={(event) => setNewAgent(normalizeAgentChoice(event.target.value))}
            >
              {AGENT_HOOK_AGENTS.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </>
        )}
        <input
          type="text"
          aria-label="Conteúdo do novo hook"
          placeholder={valuePlaceholder}
          value={newValue}
          onChange={(event) => setNewValue(event.target.value)}
        />
        <button
          type="button"
          className="button button--quiet"
          disabled={!parsed.hooks || !newValue.trim()}
          onClick={addHookEntry}
        >
          Adicionar hook
        </button>
      </div>
      <label htmlFor={id} className="code-settings__label">Hooks deste arquivo (JSON)</label>
      <textarea id={id} className="code-settings__code" spellCheck={false} rows={10} value={text}
        onChange={(event) => { setText(event.target.value); setSaved(false) }} />
      {parsed.error && <p className="code-settings__warning" role="status">{parsed.error}</p>}
      <div className="code-settings__actions">
        <button type="button" className="button button--primary" disabled={busy || !parsed.hooks || text === baseline} onClick={() => void save()}>
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
  async function save(layer: WritableLayer, value: Record<string, unknown>) {
    try {
      const report = await saveHooks(client, layer, value, conversationId)
      hooks.reload() // refresh the list in force; the layer's editor keeps the saved text it got back
      return { hooks: report.editable[layer]?.hooks }
    } catch (error) {
      return { failure: describeError(error, 'salvar os hooks') }
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
                      <td>{hook.event}</td><td><code>{hook.matcher || '*'}</code></td>
                      <td>{hook.type === 'agent' ? `agent (${hook.agent || 'explore'})` : hook.type}</td>
                      <td>{hook.source}</td>
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
        return entry ? <LayerEditor key={layer} layer={layer} entry={entry} onSave={save} /> : null
      })}
    </SettingsSection>
  )
}
