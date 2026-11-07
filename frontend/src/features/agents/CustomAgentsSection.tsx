import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  createCustomAgent,
  deleteCustomAgent,
  listCustomAgents,
  updateCustomAgent,
  type CustomAgent,
  type CustomAgentDraft,
} from '../../api/customAgents'
import { ApiError } from '../../api/errors'
import { listProviderModels, PROVIDER_NAMES, type ProviderModel, type ProviderName } from '../../api/providers'
import { Card, ErrorNote, type describeError } from '../codeSettings/shared'
import { SettingsSection } from '../settings/SettingsSection'

export const AGENT_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/
const RESERVED = new Set(['explore', 'plan', 'general-purpose', 'general', 'fork', 'verifier'])
const TOOL_NAME = /^[A-Za-z_][\w.:*-]*$/
const PERMISSION_MODES: ReadonlyArray<{ id: string; label: string }> = [
  { id: 'default', label: 'Pedir permissão' },
  { id: 'acceptEdits', label: 'Aceitar edições' },
  { id: 'plan', label: 'Só planejar (não altera nada)' },
  { id: 'dontAsk', label: 'Recusar o que pediria permissão' },
  { id: 'auto', label: 'Automático' },
  { id: 'bypassPermissions', label: 'Sem pedir permissão' },
]
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
const PROVIDER_LABELS: Record<ProviderName, string> = {
  openai: 'OpenAI',
  'openai-codex': 'OpenAI Codex (Assinatura)',
  'google-antigravity': 'Google Antigravity (Assinatura)',
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA NIM',
  opencode: 'OpenCode',
  omniroute: 'OmniRoute',
  ollama: 'Ollama (no computador)',
}

export type AgentForm = {
  name: string
  description: string
  prompt: string
  provider: string
  models: string[]
  tools: string
  disallowed: string
  effort: string
  permissionMode: string
  maxTurns: string
  enabled: boolean
}

const EMPTY: AgentForm = {
  name: '', description: '', prompt: '', provider: '', models: [], tools: '', disallowed: '', effort: '',
  permissionMode: '', maxTurns: '', enabled: true,
}

type Problem = ReturnType<typeof describeError>

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider as ProviderName] ?? provider
}

function names(text: string): string[] {
  return text.split(/[,\n]/).map((item) => item.trim()).filter(Boolean)
}

/** What is wrong with the form before it is sent (the server checks again, including the provider's catalog). */
export function customAgentProblems(form: AgentForm, { creating }: { creating: boolean }): string[] {
  const problems: string[] = []
  if (creating && (!AGENT_NAME.test(form.name) || RESERVED.has(form.name))) {
    problems.push('Nome: letras minúsculas, números, - e _ (até 64), e não pode ser o de um agente do Orin (explore, plan, general, fork, verifier).')
  }
  if (!form.description.trim()) problems.push('Diga quando usar o agente: é isso que o agente principal lê para decidir chamá-lo.')
  if (!form.prompt.trim()) problems.push('Escreva as instruções do agente.')
  if (form.provider && form.models.length === 0) problems.push('Escolha pelo menos um modelo deste provedor.')
  if (form.models.length > 16) problems.push('No máximo 16 modelos.')
  if (form.maxTurns && !(Number.isInteger(Number(form.maxTurns)) && Number(form.maxTurns) >= 1 && Number(form.maxTurns) <= 500)) {
    problems.push('Máximo de turnos: de 1 a 500.')
  }
  const badTool = [...names(form.tools), ...names(form.disallowed)].find((tool) => !TOOL_NAME.test(tool))
  if (badTool) problems.push(`"${badTool}" não é um nome de ferramenta.`)
  return problems
}

export function draftOf(form: AgentForm): CustomAgentDraft {
  const tools = names(form.tools)
  return {
    description: form.description.trim(),
    prompt: form.prompt.trim(),
    provider: form.provider || null,
    models: form.provider ? form.models : [],
    tools: tools.length > 0 ? tools : null,
    disallowedTools: names(form.disallowed),
    effort: form.effort || null,
    permissionMode: form.permissionMode || null,
    maxTurns: form.maxTurns ? Number(form.maxTurns) : null,
    enabled: form.enabled,
  }
}

function formOf(agent: CustomAgent): AgentForm {
  return {
    name: agent.name, description: agent.description, prompt: agent.prompt, provider: agent.provider ?? '',
    models: [...agent.models], tools: (agent.tools ?? []).join(', '), disallowed: agent.disallowedTools.join(', '),
    effort: agent.effort ?? '', permissionMode: agent.permissionMode ?? '', maxTurns: agent.maxTurns ? String(agent.maxTurns) : '',
    enabled: agent.enabled,
  }
}

function explain(error: unknown, action: string): Problem {
  if (error instanceof ApiError) {
    if (error.status === 422 || error.status === 400 || error.status === 409) {
      return { message: `Não foi possível ${action}:`, problems: error.problems, localOnly: false }
    }
    if (error.status === 404) return { message: 'Este agente não existe mais. Recarregue a página.', problems: [], localOnly: false }
    if (error.status === 403) return { message: `Você não tem permissão para ${action}.`, problems: [], localOnly: false }
    if (error.status === 429) return { message: 'Muitas alterações em pouco tempo; espere um minuto e tente de novo.', problems: [], localOnly: false }
  }
  return { message: `Não foi possível ${action}.`, problems: [], localOnly: false }
}

/** The models of every provider that lists some for this person (an off provider lists none). */
function useProviderModels(client: ApiClient): { catalog: Partial<Record<string, ProviderModel[]>>; loading: boolean } {
  const [state, setState] = useState<{ catalog: Partial<Record<string, ProviderModel[]>>; loading: boolean }>({ catalog: {}, loading: true })
  useEffect(() => {
    const controller = new AbortController()
    void Promise.allSettled(PROVIDER_NAMES.map((provider) => listProviderModels(client, provider, controller.signal))).then((results) => {
      if (controller.signal.aborted) return
      const catalog: Partial<Record<string, ProviderModel[]>> = {}
      results.forEach((result, index) => {
        if (result.status !== 'fulfilled') return
        const models = result.value.filter((model) => !model.model_id.toLowerCase().endsWith(':batch'))
        if (models.length > 0) catalog[PROVIDER_NAMES[index]] = models
      })
      setState({ catalog, loading: false })
    })
    return () => controller.abort()
  }, [client])
  return state
}

function ModelChecklist({ models, selected, onChange }: { models: ProviderModel[]; selected: string[]; onChange: (next: string[]) => void }) {
  const [filter, setFilter] = useState('')
  const needle = filter.trim().toLowerCase()
  const known = new Set(models.map((model) => model.model_id))
  const missing = selected.filter((id) => !known.has(id))
  const shown = models.filter((model) => !needle || model.model_id.toLowerCase().includes(needle) || model.display_name.toLowerCase().includes(needle))
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id])
  const makeDefault = (id: string) => onChange([id, ...selected.filter((item) => item !== id)])
  return (
    <div className="custom-agents__models">
      {selected.length > 0 && (
        <ol className="custom-agents__chosen" aria-label="Modelos escolhidos">
          {selected.map((id, index) => (
            <li key={id}>
              <code>{id}</code>
              {index === 0 ? <span className="custom-agents__default">padrão</span> : (
                <button type="button" className="ghost-button" onClick={() => makeDefault(id)}>Tornar padrão</button>
              )}
              {!known.has(id) && <span className="code-settings__warning">fora do catálogo</span>}
              <button type="button" className="ghost-button" aria-label={`Remover ${id}`} onClick={() => toggle(id)}>Remover</button>
            </li>
          ))}
        </ol>
      )}
      {models.length > 8 && (
        <input type="search" value={filter} placeholder="Filtrar modelos" aria-label="Filtrar modelos" onChange={(event) => setFilter(event.target.value)} />
      )}
      <ul className="custom-agents__catalog" aria-label="Modelos do provedor">
        {shown.slice(0, 200).map((model) => (
          <li key={model.model_id}>
            <label>
              <input type="checkbox" checked={selected.includes(model.model_id)} onChange={() => toggle(model.model_id)} />
              <span>{model.display_name || model.model_id}</span>
              {model.display_name && model.display_name !== model.model_id && <code>{model.model_id}</code>}
            </label>
          </li>
        ))}
      </ul>
      {missing.length > 0 && <p className="code-settings__warning">Atualize o catálogo do provedor ou remova os modelos marcados "fora do catálogo".</p>}
    </div>
  )
}

/**
 * Biblioteca › Agentes: the subagents the person makes for their Orin user (in the chat and in Code, with or without a
 * folder). Each one says when to use it, its instructions, and the provider and models it runs on; the agent that
 * calls it can only pick one of those models.
 */
export function CustomAgentsSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const { catalog, loading: modelsLoading } = useProviderModels(apiClient)
  const [agents, setAgents] = useState<CustomAgent[] | null>(null)
  const [loadError, setLoadError] = useState<Problem | null>(null)
  const [generation, setGeneration] = useState(0)
  const [form, setForm] = useState<AgentForm | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Problem | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [tried, setTried] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    listCustomAgents(apiClient, controller.signal)
      .then((items) => { setAgents(items); setLoadError(null) })
      .catch((caught: unknown) => { if (!controller.signal.aborted) { setAgents(null); setLoadError(explain(caught, 'ler os seus agentes')) } })
    return () => controller.abort()
  }, [apiClient, generation])
  const reload = useCallback(() => setGeneration((value) => value + 1), [])

  const problems = form ? customAgentProblems(form, { creating: editing === null }) : []
  const set = (patch: Partial<AgentForm>) => setForm((current) => (current ? { ...current, ...patch } : current))
  const providers = Object.keys(catalog)
  const providerOptions = form?.provider && !providers.includes(form.provider) ? [...providers, form.provider] : providers

  function close() {
    setForm(null)
    setEditing(null)
    setError(null)
    setTried(false)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!form) return
    if (problems.length > 0) { setTried(true); return }
    setBusy(true)
    setError(null)
    try {
      if (editing === null) await createCustomAgent(apiClient, form.name, draftOf(form))
      else await updateCustomAgent(apiClient, editing, draftOf(form))
      close()
      reload()
    } catch (caught) {
      setError(explain(caught, 'salvar o agente'))
    } finally {
      setBusy(false)
    }
  }

  async function toggleEnabled(agent: CustomAgent) {
    setBusy(true)
    setError(null)
    try {
      await updateCustomAgent(apiClient, agent.name, { ...draftOf(formOf(agent)), enabled: !agent.enabled })
      reload()
    } catch (caught) {
      setError(explain(caught, agent.enabled ? 'desligar o agente' : 'ligar o agente'))
    } finally {
      setBusy(false)
    }
  }

  async function remove(agent: CustomAgent) {
    setBusy(true)
    setError(null)
    try {
      await deleteCustomAgent(apiClient, agent.name)
      setConfirming(null)
      reload()
    } catch (caught) {
      setError(explain(caught, 'apagar o agente'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsSection
      eyebrow="BIBLIOTECA / AGENTES"
      title="Agentes"
      lede="Especialistas que o agente principal pode chamar, no chat e no Code. Cada um roda só no provedor e nos modelos que você escolher. Para pedir um agente numa mensagem, escreva @nome-do-agente."
      actions={!form && agents !== null && (
        <button type="button" className="button button--primary" onClick={() => { setForm({ ...EMPTY }); setEditing(null); setError(null) }}>
          Novo agente
        </button>
      )}
    >
      <div className="custom-agents" data-testid="custom-agents">
        <ErrorNote error={loadError} />
        {agents === null && !loadError && <p className="code-settings__muted">Carregando…</p>}
        {agents !== null && !form && (
          <Card title="Seus agentes" id="custom-agents-list">
            {agents.length === 0 ? (
              <p className="code-settings__empty">Você ainda não criou nenhum agente.</p>
            ) : (
              <ul className="code-settings__agents">
                {agents.map((agent) => (
                  <li key={agent.name} className={`code-settings__agent${agent.enabled ? '' : ' custom-agents__item--off'}`} data-testid={`custom-agent-${agent.name}`}>
                    <div className="code-settings__agent-head">
                      <strong>@{agent.name}</strong>
                      {!agent.enabled && <span className="code-settings__layer">desligado</span>}
                      <span className="code-settings__muted">
                        {agent.provider
                          ? `${providerLabel(agent.provider)}: ${agent.models.join(' | ')}`
                          : 'modelo do agente que chama'}
                      </span>
                    </div>
                    <p className="code-settings__muted">{agent.description}</p>
                    <div className="code-settings__actions">
                      <button type="button" className="ghost-button" disabled={busy} onClick={() => { setForm(formOf(agent)); setEditing(agent.name); setError(null) }}>Editar</button>
                      <button type="button" className="ghost-button" disabled={busy} onClick={() => void toggleEnabled(agent)}>{agent.enabled ? 'Desligar' : 'Ligar'}</button>
                      {confirming === agent.name ? (
                        <>
                          <button type="button" className="button w7-danger-button" disabled={busy} onClick={() => void remove(agent)}>Apagar de vez</button>
                          <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</button>
                        </>
                      ) : (
                        <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(agent.name)}>Apagar</button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <ErrorNote error={error} />
            <p className="code-settings__muted">
              Agentes em arquivos (<code>.orin/agents</code>, <code>.claude/agents</code>) continuam valendo e ficam em{' '}
              <Link to="/settings/agents">Configurações › Agentes</Link>. Um agente do repositório com o mesmo nome tem prioridade.
            </p>
          </Card>
        )}
        {form && (
          <Card title={editing ? `Editar @${editing}` : 'Novo agente'} id="custom-agents-form">
            <form className="code-settings__agent-form" onSubmit={submit} noValidate>
              <label>Nome
                <input value={form.name} disabled={editing !== null} maxLength={64} placeholder="revisor"
                  onChange={(event) => set({ name: event.target.value.toLowerCase() })} />
              </label>
              <label className="custom-agents__switch">
                <input type="checkbox" checked={form.enabled} onChange={(event) => set({ enabled: event.target.checked })} />
                Ligado
              </label>
              <label className="code-settings__wide">Quando usar
                <input value={form.description} maxLength={1024} placeholder="Revisa o diff antes do commit e aponta bugs"
                  onChange={(event) => set({ description: event.target.value })} />
              </label>
              <label className="code-settings__wide">Instruções
                <textarea className="code-settings__code" rows={8} value={form.prompt} onChange={(event) => set({ prompt: event.target.value })} />
              </label>
              <label className="code-settings__wide">Provedor
                <select value={form.provider} onChange={(event) => set({ provider: event.target.value, models: [] })} disabled={modelsLoading && providerOptions.length === 0}>
                  <option value="">O mesmo do agente que chama (sem modelos fixos)</option>
                  {providerOptions.map((provider) => (
                    <option key={provider} value={provider}>
                      {providerLabel(provider)}{catalog[provider] ? '' : ' (indisponível agora)'}
                    </option>
                  ))}
                </select>
              </label>
              {modelsLoading && <p className="code-settings__muted code-settings__wide">Lendo os modelos dos seus provedores…</p>}
              {!modelsLoading && providers.length === 0 && (
                <p className="code-settings__muted code-settings__wide">Nenhum provedor ligado lista modelos. Configure um em Integrações › Provedores.</p>
              )}
              {form.provider && (
                <fieldset className="code-settings__wide custom-agents__fieldset">
                  <legend>Modelos permitidos</legend>
                  <p className="code-settings__muted">
                    O primeiro é o padrão. Com mais de um, o agente principal precisa dizer qual usar em cada chamada. Se um
                    modelo falhar ao iniciar, o agente tenta o próximo da lista, e nunca o modelo de quem chamou.
                  </p>
                  <ModelChecklist models={catalog[form.provider] ?? []} selected={form.models} onChange={(models) => set({ models })} />
                </fieldset>
              )}
              <details className="code-settings__wide custom-agents__advanced">
                <summary>Opções avançadas</summary>
                <div className="code-settings__agent-form">
                  <label>Ferramentas (vazio: todas)
                    <input value={form.tools} placeholder="Read, Grep, Glob" onChange={(event) => set({ tools: event.target.value })} />
                  </label>
                  <label>Ferramentas proibidas
                    <input value={form.disallowed} placeholder="Write, Edit" onChange={(event) => set({ disallowed: event.target.value })} />
                  </label>
                  <label>Esforço
                    <select value={form.effort} onChange={(event) => set({ effort: event.target.value })}>
                      <option value="">O do modelo</option>
                      {EFFORTS.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
                    </select>
                  </label>
                  <label>Permissão (nunca maior que a de quem chama)
                    <select value={form.permissionMode} onChange={(event) => set({ permissionMode: event.target.value })}>
                      <option value="">A de quem chama</option>
                      {PERMISSION_MODES.map((mode) => <option key={mode.id} value={mode.id}>{mode.label}</option>)}
                    </select>
                  </label>
                  <label>Máximo de turnos
                    <input inputMode="numeric" value={form.maxTurns} onChange={(event) => set({ maxTurns: event.target.value.replace(/\D/g, '') })} />
                  </label>
                </div>
              </details>
              {tried && problems.length > 0 && (
                <ul className="code-settings__problems code-settings__wide" role="status">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
              )}
              <div className="code-settings__actions code-settings__wide">
                <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Salvando…' : 'Salvar'}</button>
                <button type="button" className="ghost-button" disabled={busy} onClick={close}>Cancelar</button>
              </div>
            </form>
            <ErrorNote error={error} />
          </Card>
        )}
      </div>
    </SettingsSection>
  )
}
