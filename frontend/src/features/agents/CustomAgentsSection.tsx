import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
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
import { ErrorNote, type describeError } from '../codeSettings/shared'

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

/** Starting points for a new agent: they fill what to do and which tools, never a provider (that is the person's). */
export const AGENT_TEMPLATES: ReadonlyArray<{ id: string; title: string; summary: string; form: Partial<AgentForm> }> = [
  {
    id: 'revisor',
    title: 'Revisor de código',
    summary: 'Lê o diff e aponta bugs, sem alterar arquivos.',
    form: {
      name: 'revisor',
      description: 'Revisa um diff ou trecho de código antes do commit e aponta bugs reais com caminho:linha.',
      prompt: 'Você revisa código. Leia as mudanças pedidas, procure bugs reais (lógica, segurança, concorrência, casos de borda) '
        + 'e responda com uma lista curta: gravidade, caminho:linha, o cenário que falha e a correção sugerida. '
        + 'Não altere arquivos e não comente estilo.',
      tools: 'Read, Grep, Glob',
      permissionMode: 'plan',
    },
  },
  {
    id: 'pesquisador',
    title: 'Pesquisador',
    summary: 'Busca na web e na documentação e resume com fontes.',
    form: {
      name: 'pesquisador',
      description: 'Pesquisa uma dúvida técnica na web e na documentação e devolve um resumo com as fontes.',
      prompt: 'Você pesquisa. Procure fontes confiáveis e atuais sobre a pergunta, compare o que encontrar e responda com um '
        + 'resumo objetivo, citando o link de cada afirmação. Diga claramente quando não tiver certeza.',
      tools: 'WebSearch, WebFetch, Read, Grep, Glob',
    },
  },
  {
    id: 'testador',
    title: 'Testador',
    summary: 'Roda os testes e explica cada falha.',
    form: {
      name: 'testador',
      description: 'Roda os testes e as checagens do projeto e explica cada falha com a causa provável.',
      prompt: 'Você verifica. Descubra como o projeto roda testes, lint e build, execute-os e relate o que passou e o que falhou, '
        + 'com a saída relevante e a causa provável de cada falha. Não corrija nada.',
      tools: 'Bash, Read, Grep, Glob',
      disallowed: 'Write, Edit',
    },
  },
]

type Problem = ReturnType<typeof describeError>
type Field = 'name' | 'description' | 'prompt' | 'models' | 'maxTurns' | 'tools'

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider as ProviderName] ?? provider
}

function names(text: string): string[] {
  return text.split(/[,\n]/).map((item) => item.trim()).filter(Boolean)
}

/** A typed name made valid as it is typed: lower case, no accents, spaces become "-". */
export function normalizeAgentName(text: string): string {
  return text
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9_-]/g, '')
    .slice(0, 64)
}

/** What is wrong with each field before the form is sent (the server checks again, including the provider's catalog). */
export function customAgentFieldProblems(form: AgentForm, { creating }: { creating: boolean }): Array<{ field: Field; message: string }> {
  const problems: Array<{ field: Field; message: string }> = []
  if (creating && (!AGENT_NAME.test(form.name) || RESERVED.has(form.name))) {
    problems.push({
      field: 'name',
      message: RESERVED.has(form.name)
        ? 'Este nome é de um agente do Orin (explore, plan, general, fork, verifier). Escolha outro.'
        : 'Nome: letras minúsculas, números, - e _ (até 64), começando por letra ou número.',
    })
  }
  if (!form.description.trim()) problems.push({ field: 'description', message: 'Diga quando usar o agente: é isso que o agente principal lê para decidir chamá-lo.' })
  if (!form.prompt.trim()) problems.push({ field: 'prompt', message: 'Escreva as instruções do agente.' })
  if (form.provider && form.models.length === 0) problems.push({ field: 'models', message: 'Escolha pelo menos um modelo deste provedor.' })
  if (form.models.length > 16) problems.push({ field: 'models', message: 'No máximo 16 modelos.' })
  if (form.maxTurns && !(Number.isInteger(Number(form.maxTurns)) && Number(form.maxTurns) >= 1 && Number(form.maxTurns) <= 500)) {
    problems.push({ field: 'maxTurns', message: 'Máximo de turnos: de 1 a 500.' })
  }
  const badTool = [...names(form.tools), ...names(form.disallowed)].find((tool) => !TOOL_NAME.test(tool))
  if (badTool) problems.push({ field: 'tools', message: `"${badTool}" não é um nome de ferramenta.` })
  return problems
}

export function customAgentProblems(form: AgentForm, options: { creating: boolean }): string[] {
  return customAgentFieldProblems(form, options).map((problem) => problem.message)
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

function initial(name: string): string {
  return (name.match(/[a-z0-9]/)?.[0] ?? '?').toUpperCase()
}

function FieldHint({ id, error, children }: { id: string; error?: string; children?: ReactNode }) {
  if (error) return <span id={id} className="agents-studio__field-error" role="alert">{error}</span>
  return children ? <span id={id} className="agents-studio__field-hint">{children}</span> : null
}

function ModelChecklist({ models, selected, onChange }: { models: ProviderModel[]; selected: string[]; onChange: (next: string[]) => void }) {
  const [filter, setFilter] = useState('')
  const needle = filter.trim().toLowerCase()
  const known = new Set(models.map((model) => model.model_id))
  const missing = selected.filter((id) => !known.has(id))
  const ordered = [...models].sort((a, b) => Number(b.is_favorite) - Number(a.is_favorite))
  const shown = ordered.filter((model) => !needle || model.model_id.toLowerCase().includes(needle) || model.display_name.toLowerCase().includes(needle))
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id])
  const makeDefault = (id: string) => onChange([id, ...selected.filter((item) => item !== id)])
  return (
    <div className="agents-studio__models">
      {selected.length > 0 ? (
        <ol className="agents-studio__chosen" aria-label="Modelos escolhidos">
          {selected.map((id, index) => (
            <li key={id}>
              <span className="agents-studio__chosen-order" aria-label={`${index + 1}º`}>{index + 1}</span>
              <code>{id}</code>
              {index > 0 && (
                <button type="button" className="agents-studio__link-button" onClick={() => makeDefault(id)}>Pôr em 1º</button>
              )}
              {!known.has(id) && <span className="agents-studio__pill agents-studio__pill--warn">fora do catálogo</span>}
              <button type="button" className="agents-studio__icon-button" aria-label={`Remover ${id}`} title="Remover" onClick={() => toggle(id)}>×</button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="agents-studio__field-hint">Marque abaixo os modelos em que este agente pode rodar.</p>
      )}
      {models.length > 8 && (
        <input className="agents-studio__search" type="search" value={filter} placeholder={`Filtrar ${models.length} modelos`}
          aria-label="Filtrar modelos" onChange={(event) => setFilter(event.target.value)} />
      )}
      <ul className="agents-studio__catalog" aria-label="Modelos do provedor">
        {shown.slice(0, 200).map((model) => (
          <li key={model.model_id}>
            <label className={selected.includes(model.model_id) ? 'is-checked' : undefined}>
              <input type="checkbox" checked={selected.includes(model.model_id)} onChange={() => toggle(model.model_id)} />
              <span>{model.display_name || model.model_id}</span>
              {model.display_name && model.display_name !== model.model_id && <code>{model.model_id}</code>}
              {model.is_favorite && <span className="agents-studio__star" title="Favorito" aria-label="favorito">★</span>}
            </label>
          </li>
        ))}
        {shown.length === 0 && <li className="agents-studio__field-hint">Nenhum modelo com “{filter}”.</li>}
      </ul>
      {missing.length > 0 && <p className="agents-studio__field-error">Atualize o catálogo do provedor ou remova os modelos marcados “fora do catálogo”.</p>}
    </div>
  )
}

function ModelSummary({ agent }: { agent: Pick<CustomAgent, 'provider' | 'models'> }) {
  if (!agent.provider) return <span className="agents-studio__pill">modelo de quem chama</span>
  return (
    <>
      <span className="agents-studio__pill agents-studio__pill--provider">{providerLabel(agent.provider)}</span>
      {agent.models.slice(0, 3).map((id) => <span key={id} className="agents-studio__pill agents-studio__pill--model">{id}</span>)}
      {agent.models.length > 3 && <span className="agents-studio__pill">+{agent.models.length - 3}</span>}
    </>
  )
}

function EmptyState({ onStart }: { onStart: (template?: Partial<AgentForm>) => void }) {
  return (
    <div className="agents-studio__empty" data-testid="custom-agents-empty">
      <div className="agents-studio__empty-art" aria-hidden="true">
        <span>@</span>
      </div>
      <h3>Monte sua equipe de especialistas</h3>
      <p>
        Um agente é um especialista com instruções próprias, que roda no provedor e nos modelos que você escolher (por
        exemplo, um revisor no Codex e um pesquisador no Antigravity). O agente principal chama cada um quando a tarefa
        pede, e você também pode pedir um deles numa mensagem, com <code>@nome</code>.
      </p>
      <ol className="agents-studio__steps">
        <li><strong>1</strong><span>Dê um nome e diga quando usar</span></li>
        <li><strong>2</strong><span>Escreva as instruções</span></li>
        <li><strong>3</strong><span>Escolha o provedor e os modelos</span></li>
      </ol>
      <button type="button" className="button button--primary" onClick={() => onStart()}>Criar o primeiro agente</button>
      <p className="agents-studio__templates-label">Ou comece de um modelo pronto:</p>
      <div className="agents-studio__templates">
        {AGENT_TEMPLATES.map((template) => (
          <button key={template.id} type="button" className="agents-studio__template" onClick={() => onStart(template.form)}>
            <strong>{template.title}</strong>
            <span>{template.summary}</span>
          </button>
        ))}
      </div>
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
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    listCustomAgents(apiClient, controller.signal)
      .then((items) => { setAgents(items); setLoadError(null) })
      .catch((caught: unknown) => { if (!controller.signal.aborted) { setAgents(null); setLoadError(explain(caught, 'ler os seus agentes')) } })
    return () => controller.abort()
  }, [apiClient, generation])
  const reload = useCallback(() => setGeneration((value) => value + 1), [])

  const creating = editing === null
  const fieldProblems = form ? customAgentFieldProblems(form, { creating }) : []
  const takenName = Boolean(form && creating && agents?.some((agent) => agent.name === form.name))
  const problemOf = (field: Field): string | undefined => {
    if (field === 'name' && takenName) return `Você já tem um agente @${form?.name}. Escolha outro nome ou edite o existente.`
    return tried ? fieldProblems.find((problem) => problem.field === field)?.message : undefined
  }
  const set = (patch: Partial<AgentForm>) => setForm((current) => (current ? { ...current, ...patch } : current))
  const providers = Object.keys(catalog)
  const providerOptions = form?.provider && !providers.includes(form.provider) ? [...providers, form.provider] : providers
  const pristine = form !== null && creating && JSON.stringify(form) === JSON.stringify(EMPTY)

  function open(next: AgentForm, name: string | null) {
    setForm(next)
    setEditing(name)
    setError(null)
    setTried(false)
    setSaved(null)
    setConfirming(null)
  }

  function startNew(template?: Partial<AgentForm>) {
    const base = { ...EMPTY, ...template }
    // A template's name that is already taken gets a number, so the form starts valid.
    if (template?.name && agents?.some((agent) => agent.name === base.name)) {
      let index = 2
      while (agents.some((agent) => agent.name === `${template.name}-${index}`)) index += 1
      base.name = `${template.name}-${index}`
    }
    open(base, null)
  }

  function close() {
    setForm(null)
    setEditing(null)
    setError(null)
    setTried(false)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!form) return
    if (fieldProblems.length > 0 || takenName) { setTried(true); return }
    setBusy(true)
    setError(null)
    try {
      if (creating) await createCustomAgent(apiClient, form.name, draftOf(form))
      else await updateCustomAgent(apiClient, form.name, draftOf(form))
      setSaved(form.name)
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
      setSaved(null)
      reload()
    } catch (caught) {
      setError(explain(caught, 'apagar o agente'))
    } finally {
      setBusy(false)
    }
  }

  const fileAgentsNote = (
    <p className="agents-studio__footnote">
      Agentes em arquivos (<code>.orin/agents</code>, <code>.claude/agents</code>) continuam valendo e ficam em{' '}
      <Link to="/settings/agents">Configurações › Agentes</Link>. Um agente do repositório com o mesmo nome tem prioridade.
    </p>
  )

  return (
    <div className="agents-studio" data-testid="custom-agents">
      {!form && agents !== null && agents.length > 0 && (
        <div className="agents-studio__intro">
          <div>
            <h2>Agentes especialistas</h2>
            <p>
              Valem no chat e no Orin Code. O agente principal chama cada um quando a tarefa pede, sempre num dos modelos
              que você permitiu. Para pedir um agente numa mensagem, escreva <code>@nome</code>.
            </p>
          </div>
          <button type="button" className="button button--primary" onClick={() => startNew()}>
            <span aria-hidden="true">+ </span>Novo agente
          </button>
        </div>
      )}

      <ErrorNote error={loadError} />
      {agents === null && !loadError && (
        <div className="agents-studio__grid" aria-busy="true" aria-label="Carregando agentes">
          {[0, 1].map((index) => <div key={index} className="agents-studio__card agents-studio__card--skeleton" />)}
        </div>
      )}

      {agents !== null && !form && (
        <>
          {saved && agents.some((agent) => agent.name === saved) && (
            <p className="agents-studio__toast" role="status">
              <strong>@{saved}</strong> salvo. Peça numa mensagem com <code>@{saved}</code> ou deixe o agente principal decidir.
            </p>
          )}
          <ErrorNote error={error} />
          {agents.length === 0 ? (
            <EmptyState onStart={startNew} />
          ) : (
            <ul className="agents-studio__grid" aria-label="Seus agentes">
              {agents.map((agent) => (
                <li key={agent.name} className={`agents-studio__card${agent.enabled ? '' : ' is-off'}`} data-testid={`custom-agent-${agent.name}`}>
                  <div className="agents-studio__card-head">
                    <span className="agents-studio__avatar" aria-hidden="true">{initial(agent.name)}</span>
                    <div className="agents-studio__card-title">
                      <strong>@{agent.name}</strong>
                      <span>{agent.enabled ? 'Ligado' : 'Desligado'}</span>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={agent.enabled}
                      aria-label={agent.enabled ? `Desligar @${agent.name}` : `Ligar @${agent.name}`}
                      className="agents-studio__switch"
                      disabled={busy}
                      onClick={() => void toggleEnabled(agent)}
                    >
                      <span />
                    </button>
                  </div>
                  <p className="agents-studio__card-description">{agent.description}</p>
                  <div className="agents-studio__chips"><ModelSummary agent={agent} /></div>
                  <div className="agents-studio__card-actions">
                    {confirming === agent.name ? (
                      <>
                        <span className="agents-studio__confirm-text">Apagar @{agent.name}?</span>
                        <button type="button" className="button w7-danger-button" disabled={busy} onClick={() => void remove(agent)}>Apagar de vez</button>
                        <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</button>
                      </>
                    ) : (
                      <>
                        <button type="button" className="ghost-button" disabled={busy} onClick={() => open(formOf(agent), agent.name)}>Editar</button>
                        <button type="button" className="ghost-button agents-studio__danger-link" disabled={busy} onClick={() => setConfirming(agent.name)}>Apagar</button>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {fileAgentsNote}
        </>
      )}

      {form && (
        <div className="agents-studio__editor" data-testid="custom-agents-form">
          <div className="agents-studio__editor-top">
            <button type="button" className="agents-studio__back" onClick={close} disabled={busy}>← Voltar para os agentes</button>
            <h2>{creating ? 'Novo agente' : `Editar @${editing}`}</h2>
          </div>

          {pristine && (
            <div className="agents-studio__template-strip">
              <span>Começar de um modelo:</span>
              {AGENT_TEMPLATES.map((template) => (
                <button key={template.id} type="button" className="agents-studio__chip-button" onClick={() => startNew(template.form)}>
                  {template.title}
                </button>
              ))}
            </div>
          )}

          <div className="agents-studio__editor-body">
            <form className="agents-studio__form" onSubmit={submit} noValidate>
              <section className="agents-studio__section">
                <header><span>1</span><div><h3>Identidade</h3><p>Como você e o agente principal vão reconhecê-lo.</p></div></header>
                <div className="agents-studio__row">
                  <label className="agents-studio__field">
                    <span className="agents-studio__label">Nome</span>
                    <span className="agents-studio__prefixed">
                      <span aria-hidden="true">@</span>
                      <input value={form.name} disabled={!creating} maxLength={64} placeholder="revisor" aria-label="Nome"
                        aria-invalid={Boolean(problemOf('name'))} aria-describedby="agent-name-hint"
                        onChange={(event) => set({ name: normalizeAgentName(event.target.value) })} />
                    </span>
                    <FieldHint id="agent-name-hint" error={problemOf('name')}>
                      {creating ? 'Minúsculas, números, - e _. Não dá para mudar depois.' : 'O nome não muda depois de criado.'}
                    </FieldHint>
                  </label>
                  <label className="agents-studio__toggle">
                    <input type="checkbox" checked={form.enabled} onChange={(event) => set({ enabled: event.target.checked })} />
                    <span>Ligado</span>
                  </label>
                </div>
                <label className="agents-studio__field">
                  <span className="agents-studio__label">Quando usar</span>
                  <input value={form.description} maxLength={1024} placeholder="Revisa o diff antes do commit e aponta bugs" aria-label="Quando usar"
                    aria-invalid={Boolean(problemOf('description'))} aria-describedby="agent-description-hint"
                    onChange={(event) => set({ description: event.target.value })} />
                  <FieldHint id="agent-description-hint" error={problemOf('description')}>
                    É o que o agente principal lê para decidir chamá-lo. Seja específico.
                  </FieldHint>
                </label>
              </section>

              <section className="agents-studio__section">
                <header><span>2</span><div><h3>Instruções</h3><p>O que o agente deve fazer e como responder.</p></div></header>
                <label className="agents-studio__field">
                  <span className="agents-studio__label">Instruções</span>
                  <textarea rows={9} value={form.prompt} aria-label="Instruções" aria-invalid={Boolean(problemOf('prompt'))}
                    aria-describedby="agent-prompt-hint"
                    placeholder="Você revisa código. Leia o diff, aponte bugs com caminho:linha e não altere arquivos."
                    onChange={(event) => set({ prompt: event.target.value })} />
                  <FieldHint id="agent-prompt-hint" error={problemOf('prompt')}>
                    {form.prompt.length > 0 ? `${form.prompt.length.toLocaleString('pt-BR')} caracteres` : undefined}
                  </FieldHint>
                </label>
              </section>

              <section className="agents-studio__section">
                <header><span>3</span><div><h3>Provedor e modelos</h3><p>Onde ele roda. Ele nunca cai no modelo de quem chamou.</p></div></header>
                <div className="agents-studio__providers" role="radiogroup" aria-label="Provedor">
                  <label className={`agents-studio__provider${form.provider === '' ? ' is-selected' : ''}`}>
                    <input type="radio" name="agent-provider" value="" checked={form.provider === ''} onChange={() => set({ provider: '', models: [] })} />
                    <strong>O mesmo de quem chama</strong>
                    <span>sem modelos fixos</span>
                  </label>
                  {providerOptions.map((provider) => (
                    <label key={provider} className={`agents-studio__provider${form.provider === provider ? ' is-selected' : ''}${catalog[provider] ? '' : ' is-unavailable'}`}>
                      <input type="radio" name="agent-provider" value={provider} checked={form.provider === provider}
                        onChange={() => set({ provider, models: [] })} />
                      <strong>{providerLabel(provider)}</strong>
                      <span>{catalog[provider] ? `${catalog[provider]?.length} modelos` : 'indisponível agora'}</span>
                    </label>
                  ))}
                </div>
                {modelsLoading && <p className="agents-studio__field-hint">Lendo os modelos dos seus provedores…</p>}
                {!modelsLoading && providers.length === 0 && (
                  <p className="agents-studio__field-hint">
                    Nenhum provedor ligado lista modelos. <Link to="/integrations/providers">Configure um em Conexões › Provedores</Link>.
                  </p>
                )}
                {form.provider && (
                  <div className="agents-studio__field" aria-describedby="agent-models-hint">
                    <span className="agents-studio__label">Modelos permitidos</span>
                    <ModelChecklist models={catalog[form.provider] ?? []} selected={form.models} onChange={(models) => set({ models })} />
                    <FieldHint id="agent-models-hint" error={problemOf('models')}>
                      Com um modelo só, ele roda direto nele. Com mais de um, quem chama escolhe um a cada chamada; se o
                      escolhido falhar ao iniciar, o agente tenta os outros, nesta ordem.
                    </FieldHint>
                  </div>
                )}
              </section>

              <details className="agents-studio__section agents-studio__advanced" open={Boolean(problemOf('tools') || problemOf('maxTurns')) || undefined}>
                <summary>
                  <span className="agents-studio__advanced-title">Opções avançadas</span>
                  <span className="agents-studio__field-hint">Ferramentas, esforço, permissão e limite de turnos</span>
                </summary>
                <div className="agents-studio__grid-2">
                  <label className="agents-studio__field">
                    <span className="agents-studio__label">Ferramentas permitidas</span>
                    <input value={form.tools} placeholder="Vazio: todas as de quem chama" aria-label="Ferramentas permitidas"
                      onChange={(event) => set({ tools: event.target.value })} />
                    <FieldHint id="agent-tools-hint" error={problemOf('tools')}>Separadas por vírgula, ex.: Read, Grep, Glob</FieldHint>
                  </label>
                  <label className="agents-studio__field">
                    <span className="agents-studio__label">Ferramentas proibidas</span>
                    <input value={form.disallowed} placeholder="Write, Edit" aria-label="Ferramentas proibidas"
                      onChange={(event) => set({ disallowed: event.target.value })} />
                  </label>
                  <label className="agents-studio__field">
                    <span className="agents-studio__label">Esforço</span>
                    <select value={form.effort} aria-label="Esforço" onChange={(event) => set({ effort: event.target.value })}>
                      <option value="">O do modelo</option>
                      {EFFORTS.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
                    </select>
                  </label>
                  <label className="agents-studio__field">
                    <span className="agents-studio__label">Permissão</span>
                    <select value={form.permissionMode} aria-label="Permissão" onChange={(event) => set({ permissionMode: event.target.value })}>
                      <option value="">A de quem chama</option>
                      {PERMISSION_MODES.map((mode) => <option key={mode.id} value={mode.id}>{mode.label}</option>)}
                    </select>
                    <FieldHint id="agent-permission-hint">Nunca fica maior que a de quem chama.</FieldHint>
                  </label>
                  <label className="agents-studio__field">
                    <span className="agents-studio__label">Máximo de turnos</span>
                    <input inputMode="numeric" value={form.maxTurns} placeholder="Sem limite próprio" aria-label="Máximo de turnos"
                      onChange={(event) => set({ maxTurns: event.target.value.replace(/\D/g, '') })} />
                    <FieldHint id="agent-turns-hint" error={problemOf('maxTurns')} />
                  </label>
                </div>
              </details>

              <ErrorNote error={error} />
              <div className="agents-studio__savebar">
                {tried && (fieldProblems.length > 0 || takenName) && (
                  <span className="agents-studio__field-error" role="status">Corrija os campos marcados para salvar.</span>
                )}
                <button type="button" className="ghost-button" disabled={busy} onClick={close}>Cancelar</button>
                <button type="submit" className="button button--primary" disabled={busy}>
                  {busy ? 'Salvando…' : creating ? 'Criar agente' : 'Salvar'}
                </button>
              </div>
            </form>

            <aside className="agents-studio__preview" aria-label="Prévia do agente">
              <p className="agents-studio__preview-label">Prévia</p>
              <div className={`agents-studio__card agents-studio__card--preview${form.enabled ? '' : ' is-off'}`}>
                <div className="agents-studio__card-head">
                  <span className="agents-studio__avatar" aria-hidden="true">{initial(form.name)}</span>
                  <div className="agents-studio__card-title">
                    <strong>@{form.name || 'nome'}</strong>
                    <span>{form.enabled ? 'Ligado' : 'Desligado'}</span>
                  </div>
                </div>
                <p className="agents-studio__card-description">{form.description || 'Quando usar este agente…'}</p>
                <div className="agents-studio__chips"><ModelSummary agent={{ provider: form.provider || null, models: form.models }} /></div>
              </div>
              <p className="agents-studio__preview-label">Numa mensagem</p>
              <div className="agents-studio__bubble">
                <code>@{form.name || 'nome'}</code> cuide desta parte pra mim
              </div>
              <p className="agents-studio__field-hint">
                {form.provider && form.models.length > 1
                  ? `Quem chamar escolhe um destes ${form.models.length} modelos a cada chamada.`
                  : form.provider && form.models.length === 1
                    ? `Sempre roda em ${form.models[0]}.`
                    : 'Roda no mesmo modelo do agente que o chamar.'}
              </p>
            </aside>
          </div>
        </div>
      )}
    </div>
  )
}
