import { useCallback, useState, type FormEvent } from 'react'
import { deleteAgent, getAgents, saveAgent, type AgentDefinition, type AgentDraft } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, FolderBanner, useFolderConversation, useLoaded, useSettingsClient } from './shared'

export const AGENT_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/
const PERMISSION_MODES = ['default', 'acceptEdits', 'plan', 'dontAsk', 'bypassPermissions', 'auto'] as const
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
const ORIGIN: Record<string, string> = { user: 'usuário', project: 'projeto', plugin: 'plugin', builtin: 'do Orin', cli: 'linha de comando' }

type Form = { name: string; scope: 'user' | 'project'; description: string; prompt: string; tools: string; disallowed: string; model: string; permissionMode: string; maxTurns: string; effort: string }

const EMPTY: Form = { name: '', scope: 'user', description: '', prompt: '', tools: '', disallowed: '', model: '', permissionMode: '', maxTurns: '', effort: '' }

function names(text: string): string[] {
  return text.split(/[,\n]/).map((item) => item.trim()).filter(Boolean)
}

/** What is wrong with the form before it is sent (the server checks again). */
export function agentFormProblems(form: Form): string[] {
  const problems: string[] = []
  if (!AGENT_NAME.test(form.name)) problems.push('Nome: letras minúsculas, números, - e _ (até 64), começando por letra ou número.')
  if (!form.description.trim()) problems.push('Diga quando usar o agente (descrição).')
  if (!form.prompt.trim()) problems.push('Escreva as instruções do agente.')
  if (form.maxTurns && !(Number.isInteger(Number(form.maxTurns)) && Number(form.maxTurns) >= 1 && Number(form.maxTurns) <= 500)) problems.push('Máximo de turnos: de 1 a 500.')
  const badTool = [...names(form.tools), ...names(form.disallowed)].find((tool) => !/^[A-Za-z_][\w.:-]*$/.test(tool))
  if (badTool) problems.push(`"${badTool}" não é um nome de ferramenta (use só nomes, sem regras entre parênteses).`)
  return problems
}

export function draftOf(form: Form): AgentDraft {
  const tools = names(form.tools)
  return {
    scope: form.scope, description: form.description.trim(), prompt: form.prompt, tools: tools.length > 0 ? tools : null,
    disallowedTools: names(form.disallowed), model: form.model.trim() || null, permissionMode: form.permissionMode || null,
    maxTurns: form.maxTurns ? Number(form.maxTurns) : null, effort: form.effort || null,
  }
}

function formOf(agent: AgentDefinition): Form {
  return {
    name: agent.name, scope: agent.origin === 'project' ? 'project' : 'user', description: agent.description, prompt: agent.prompt,
    tools: (agent.tools ?? []).join(', '), disallowed: agent.disallowedTools.join(', '), model: agent.model ?? '',
    permissionMode: agent.permissionMode ?? '', maxTurns: agent.maxTurns ? String(agent.maxTurns) : '', effort: agent.effort ?? '',
  }
}

/**
 * `/agents` (W7): the agents defined in files (the person's, the project's, plugins'), and a form to create, edit or
 * delete the person's and the project's. Files keep Claude Code's format (`.orin/agents/<name>.md`).
 */
export function AgentsSection() {
  const client = useSettingsClient()
  const conversationId = useFolderConversation()
  const load = useCallback(() => getAgents(client, conversationId), [client, conversationId])
  const agents = useLoaded(load, 'ler os agentes')
  const [form, setForm] = useState<Form | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [tried, setTried] = useState(false)

  const problems = form ? agentFormProblems(form) : []
  const set = (patch: Partial<Form>) => setForm((current) => (current ? { ...current, ...patch } : current))

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!form) return
    if (problems.length > 0) { setTried(true); return }
    setBusy(true)
    setError(null)
    try {
      await saveAgent(client, form.name, draftOf(form), conversationId, editing === null) // W7-R6: a new agent never replaces a file
      setForm(null)
      setEditing(null)
      setTried(false)
      agents.reload()
    } catch (caught) {
      setError(describeError(caught, 'salvar o agente'))
    } finally {
      setBusy(false)
    }
  }

  async function remove(agent: AgentDefinition) {
    setBusy(true)
    setError(null)
    try {
      await deleteAgent(client, agent.name, agent.origin === 'project' ? 'project' : 'user', conversationId)
      setConfirming(null)
      agents.reload()
    } catch (caught) {
      setError(describeError(caught, 'apagar o agente'))
    } finally {
      setBusy(false)
    }
  }

  const data = agents.data
  return (
    <SettingsSection eyebrow="ORIN CODE / AGENTES">
      {!agents.error?.localOnly && <FolderBanner projectRoot={null} conversationId={conversationId} />}
      <ErrorNote error={agents.error} />
      {agents.loading && !data && <p className="code-settings__muted">Carregando…</p>}
      {data && data.errors.length > 0 && (
        <div className="code-settings__error" role="alert">
          <p>Arquivos de agente com erro (ignorados):</p>
          <ul>{data.errors.map((item, index) => <li key={index}><code>{item.file}</code>: {item.message}</li>)}</ul>
        </div>
      )}
      {data && (
        <Card title="Agentes" id="agents-list" actions={!form && <button type="button" className="button button--primary" onClick={() => { setForm({ ...EMPTY, scope: conversationId ? 'project' : 'user' }); setEditing(null) }}>Novo agente</button>}>
          {data.agents.length === 0 ? <p className="code-settings__empty">Nenhum agente definido em arquivos.</p> : (
            <ul className="code-settings__agents">
              {data.agents.map((agent) => (
                <li key={`${agent.origin}:${agent.name}`} className="code-settings__agent">
                  <div className="code-settings__agent-head">
                    <strong>{agent.name}</strong>
                    <span className="code-settings__layer">{ORIGIN[agent.origin] ?? agent.origin}</span>
                    {agent.model && <span className="code-settings__muted">{agent.model}</span>}
                  </div>
                  <p className="code-settings__muted">{agent.description}</p>
                  {agent.path && <p className="code-settings__file">{agent.path}</p>}
                  {agent.editable && (
                    <div className="code-settings__actions">
                      <button type="button" className="ghost-button" disabled={busy} onClick={() => { setForm(formOf(agent)); setEditing(agent.name) }}>Editar</button>
                      {confirming === `${agent.origin}:${agent.name}` ? (
                        <>
                          <button type="button" className="button w7-danger-button" disabled={busy} onClick={() => void remove(agent)}>Apagar o arquivo</button>
                          <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</button>
                        </>
                      ) : (
                        <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(`${agent.origin}:${agent.name}`)}>Apagar</button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {data.shadowed.length > 0 && (
            <p className="code-settings__muted">Ignorados por terem o mesmo nome: {data.shadowed.map((item) => `${item.name} (${item.ignored}; vale ${item.kept})`).join(', ')}.</p>
          )}
          <ErrorNote error={form ? null : error} />
        </Card>
      )}
      {form && (
        <Card title={editing ? `Editar ${editing}` : 'Novo agente'} id="agents-form">
          <form className="code-settings__agent-form" onSubmit={submit} noValidate>
            <label>Nome
              <input value={form.name} disabled={editing !== null} maxLength={64} onChange={(event) => set({ name: event.target.value })} placeholder="revisor-de-testes" />
            </label>
            <label>Onde salvar
              <select value={form.scope} disabled={editing !== null} onChange={(event) => set({ scope: event.target.value as Form['scope'] })}>
                <option value="user">Usuário (todas as pastas)</option>
                {conversationId && <option value="project">Projeto (esta pasta)</option>}
              </select>
            </label>
            <label className="code-settings__wide">Quando usar (descrição)
              <input value={form.description} maxLength={1024} onChange={(event) => set({ description: event.target.value })} />
            </label>
            <label className="code-settings__wide">Instruções
              <textarea className="code-settings__code" rows={8} value={form.prompt} onChange={(event) => set({ prompt: event.target.value })} />
            </label>
            <label>Ferramentas (vazio: todas)
              <input value={form.tools} onChange={(event) => set({ tools: event.target.value })} placeholder="Read, Grep, Glob" />
            </label>
            <label>Ferramentas proibidas
              <input value={form.disallowed} onChange={(event) => set({ disallowed: event.target.value })} placeholder="Write, Edit" />
            </label>
            <label>Modelo (vazio: o da conversa)
              <input value={form.model} maxLength={120} onChange={(event) => set({ model: event.target.value })} />
            </label>
            <label>Modo de permissão
              <select value={form.permissionMode} onChange={(event) => set({ permissionMode: event.target.value })}>
                <option value="">O da conversa</option>
                {PERMISSION_MODES.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
              </select>
            </label>
            <label>Máximo de turnos
              <input inputMode="numeric" value={form.maxTurns} onChange={(event) => set({ maxTurns: event.target.value.replace(/\D/g, '') })} />
            </label>
            <label>Esforço
              <select value={form.effort} onChange={(event) => set({ effort: event.target.value })}>
                <option value="">O da conversa</option>
                {EFFORTS.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
              </select>
            </label>
            {tried && problems.length > 0 && (
              <ul className="code-settings__problems code-settings__wide" role="status">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
            )}
            <div className="code-settings__actions code-settings__wide">
              <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Salvando…' : 'Salvar'}</button>
              <button type="button" className="ghost-button" disabled={busy} onClick={() => { setForm(null); setEditing(null); setError(null); setTried(false) }}>Cancelar</button>
            </div>
          </form>
          <ErrorNote error={error} />
        </Card>
      )}
    </SettingsSection>
  )
}
