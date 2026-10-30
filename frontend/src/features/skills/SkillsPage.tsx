import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import {
  createSkillsClient,
  type AgentSkillMode,
  type CreateSkillInput,
  type SkillAgent,
  type SkillDetail,
  type SkillList,
  type SkillSummary,
  type SkillsClient,
} from '../../api/skills'
import { Brand } from '../../components/Brand'
import { CommandPalette } from '../../components/CommandPalette'
import { Disclosure } from '../../components/ui/Disclosure'

type SkillsPageProps = { client?: SkillsClient; embedded?: boolean }

type ListState =
  | { status: 'loading' }
  | { status: 'loaded'; value: SkillList }
  | { status: 'error' }

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; value: SkillDetail }
  | { status: 'error' }

type SkillCategoryTab = 'ready' | 'custom' | 'team'

type AvailabilityOption = 'Todos os projetos' | 'Apenas Auryly'

type InspectorSkillItem = {
  id: string
  name: string
  description: string
  version: string
  source: string
  available: boolean
  isCustom: boolean
  isShowcase: boolean
  icon: string
  defaultAvailability: AvailabilityOption
  quickExample: string
  instructions: string
  category: SkillCategoryTab
}

const SHOWCASE_SKILLS: ReadonlyArray<InspectorSkillItem> = [
  {
    id: 'showcase-relatorios',
    name: 'Criar relatórios',
    description: 'Transforma dados e notas em relatórios claros com resumo executivo.',
    version: '1.0.0',
    source: 'system',
    available: true,
    isCustom: false,
    isShowcase: true,
    icon: '📄',
    defaultAvailability: 'Todos os projetos',
    quickExample: 'Crie o relatório semanal da Auryly',
    instructions:
      '# Criar relatórios\n\n1. Analise os dados e notas enviados.\n2. Estruture com resumo executivo, destaques e próximos passos.',
    category: 'ready',
  },
  {
    id: 'showcase-revisar',
    name: 'Revisar textos',
    description: 'Corrige clareza, tom e ortografia mantendo a sua voz.',
    version: '1.0.0',
    source: 'system',
    available: true,
    isCustom: false,
    isShowcase: true,
    icon: 'Aa',
    defaultAvailability: 'Todos os projetos',
    quickExample: 'Revise este comunicado mantendo um tom direto e natural',
    instructions:
      '# Revisar textos\n\n1. Corrija gramática, fluidez e ortografia.\n2. Preserve a voz e intenção original do autor.',
    category: 'ready',
  },
  {
    id: 'showcase-planilhas',
    name: 'Analisar planilhas',
    description: 'Encontra padrões, somas e pontos fora da curva em tabelas.',
    version: '1.0.0',
    source: 'system',
    available: true,
    isCustom: false,
    isShowcase: true,
    icon: '📊',
    defaultAvailability: 'Todos os projetos',
    quickExample: 'Analise a planilha de vendas do mês e aponte anomalias',
    instructions:
      '# Analisar planilhas\n\n1. Valide colunas numéricas e somas.\n2. Destaque tendências e pontos fora da curva.',
    category: 'ready',
  },
  {
    id: 'showcase-auryly',
    name: 'Padrão Auryly',
    description: 'Aplica as cores, fontes e tom de voz da marca Auryly.',
    version: '1.0.0',
    source: 'user',
    available: true,
    isCustom: true,
    isShowcase: true,
    icon: '✦',
    defaultAvailability: 'Apenas Auryly',
    quickExample: 'Aplique o Padrão Auryly nesta proposta comercial',
    instructions:
      '# Padrão Auryly\n\n1. Aplique a paleta, tipografia e diretrizes verbais da marca Auryly.\n2. Mantenha consistência em todos os títulos e blocos.',
    category: 'custom',
  },
]

function toInspectorItem(skill: SkillSummary): InspectorSkillItem {
  const isCustom = skill.source === 'custom' || skill.source === 'user'
  return {
    id: skill.id,
    name: skill.name,
    description: skill.description,
    version: skill.version,
    source: skill.source,
    available: skill.available,
    isCustom,
    isShowcase: false,
    icon: isCustom ? '✦' : '📄',
    defaultAvailability: 'Todos os projetos',
    quickExample: `Use a habilidade ${skill.name} na tarefa atual`,
    instructions: `# ${skill.name}\n\n${skill.description}`,
    category: isCustom ? 'custom' : 'ready',
  }
}

export function SkillsPage({ client, embedded = false }: SkillsPageProps) {
  const { skillId } = useParams()
  const apiClient = useMemo(() => client ?? createSkillsClient(createBrowserApiClient()), [client])

  return skillId ? (
    <SkillDetailPage client={apiClient} skillId={skillId} embedded={embedded} />
  ) : (
    <SkillsLibrary client={apiClient} embedded={embedded} />
  )
}

export function SkillsLibrary({ client, embedded = false }: { client: SkillsClient; embedded?: boolean }) {
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('')
  const [activeTab, setActiveTab] = useState<SkillCategoryTab>('ready')
  const [list, setList] = useState<ListState>({ status: 'loading' })
  const [creating, setCreating] = useState(false)
  const [reload, setReload] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState(false)
  const [selectedSkillId, setSelectedSkillId] = useState<string>(SHOWCASE_SKILLS[0].id)
  const [instructionsOpen, setInstructionsOpen] = useState(false)
  const [availabilityBySkill, setAvailabilityBySkill] = useState<Record<string, AvailabilityOption>>({})
  const [confirmBySkill, setConfirmBySkill] = useState<Record<string, boolean>>({})

  useEffect(() => {
    const controller = new AbortController()
    client
      .list({ query, source: source || undefined }, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setList({ status: 'loaded', value })
      })
      .catch(() => {
        if (!controller.signal.aborted) setList({ status: 'error' })
      })
    return () => controller.abort()
  }, [client, query, reload, source])

  const sources =
    list.status === 'loaded' ? [...new Set(list.value.items.map((item) => item.source))].sort() : []

  const isShowcaseMode =
    list.status === 'loaded' && list.value.items.length === 0 && !query.trim()

  const displayedItems: ReadonlyArray<InspectorSkillItem> = useMemo(() => {
    if (isShowcaseMode) {
      if (activeTab === 'custom') {
        return SHOWCASE_SKILLS.filter((item) => item.isCustom)
      }
      if (activeTab === 'team') {
        return SHOWCASE_SKILLS.filter((item) => item.id === 'showcase-auryly' || item.id === 'showcase-relatorios')
      }
      return SHOWCASE_SKILLS
    }
    if (list.status === 'loaded') {
      return list.value.items.map(toInspectorItem)
    }
    return []
  }, [activeTab, isShowcaseMode, list])

  const selectedSkill: InspectorSkillItem = useMemo(() => {
    const found = displayedItems.find((item) => item.id === selectedSkillId)
    if (found) return found
    if (displayedItems.length > 0) return displayedItems[0]
    return SHOWCASE_SKILLS[0]
  }, [displayedItems, selectedSkillId])

  useEffect(() => {
    setInstructionsOpen(false)
  }, [selectedSkill.id])

  function handleSelectSkill(id: string) {
    setSelectedSkillId(id)
    setInstructionsOpen(false)
  }

  function clearPaginationForBaseRefresh() {
    setLoadingMore(false)
    setMoreError(false)
  }

  async function loadMore() {
    if (loadingMore || list.status !== 'loaded' || !list.value.next_cursor) return
    const cursor = list.value.next_cursor
    const controller = new AbortController()
    setLoadingMore(true)
    setMoreError(false)
    try {
      const next = await client.list({ query, source: source || undefined, cursor }, controller.signal)
      setList((current) =>
        current.status === 'loaded' && current.value.next_cursor === cursor
          ? {
              status: 'loaded',
              value: { items: [...current.value.items, ...next.items], next_cursor: next.next_cursor },
            }
          : current,
      )
    } catch {
      setMoreError(true)
    } finally {
      setLoadingMore(false)
    }
  }

  const currentAvailability: AvailabilityOption =
    availabilityBySkill[selectedSkill.id] ?? selectedSkill.defaultAvailability
  const currentConfirm: boolean = confirmBySkill[selectedSkill.id] ?? true

  const content = (
    <section className="skills-library skills-ref12" aria-labelledby="skills-title">
      <header className="skills-library__heading skills-ref12__header">
        <div className="skills-ref12__header-copy">
          <h1 id="skills-title" className="skills-ref12__title" data-testid="skills-heading">
            Habilidades
          </h1>
          <h2 className="visually-hidden">Skills</h2>
          <p className="skills-ref12__subtitle">
            Pacotes de instruções que ensinam o Orin a executar tarefas específicas.
          </p>
        </div>
      </header>

      <div className="skills-ref12__tabs" role="tablist" aria-label="Categorias de habilidades">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'ready'}
          className={`skills-ref12__tab${activeTab === 'ready' ? ' is-active' : ''}`}
          data-testid="skills-tab-ready"
          onClick={() => setActiveTab('ready')}
        >
          Prontas para usar
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'custom'}
          className={`skills-ref12__tab${activeTab === 'custom' ? ' is-active' : ''}`}
          data-testid="skills-tab-custom"
          onClick={() => setActiveTab('custom')}
        >
          Criadas por você
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'team'}
          className={`skills-ref12__tab${activeTab === 'team' ? ' is-active' : ''}`}
          data-testid="skills-tab-team"
          onClick={() => setActiveTab('team')}
        >
          Da equipe
        </button>
      </div>

      {creating ? (
        <CreateSkillForm
          client={client}
          onCancel={() => setCreating(false)}
          onCreated={(created) => {
            setList((current) =>
              current.status === 'loaded'
                ? { status: 'loaded', value: { ...current.value, items: [created, ...current.value.items] } }
                : current,
            )
            setCreating(false)
          }}
        />
      ) : (
        <>
          <div className="skills-ref12__toolbar">
            <label className="skills-search" htmlFor="skills-search">
              <span className="visually-hidden">Buscar skills</span>
              <input
                id="skills-search"
                type="search"
                className="skills-ref12__search-input"
                aria-label="Buscar skills"
                data-testid="skills-search-input"
                value={query}
                onChange={(event) => {
                  clearPaginationForBaseRefresh()
                  setQuery(event.target.value)
                }}
                placeholder="Buscar habilidades..."
              />
            </label>
            <div className="skills-ref12__toolbar-actions">
              <button
                type="button"
                className="button button--primary skills-ref12__create-btn"
                data-testid="skills-create-button"
                aria-label="Criar skill"
                onClick={() => setCreating(true)}
              >
                + Criar habilidade
              </button>
              <button
                type="button"
                className="button button--secondary skills-ref12__import-btn"
                data-testid="skills-import-button"
                onClick={() => setCreating(true)}
              >
                Importar arquivo
              </button>
            </div>
          </div>

          {sources.length > 0 && (
            <div className="skills-filters" aria-label="Filtrar por origem">
              <button
                type="button"
                className={`ghost-button ${source === '' ? 'is-active' : ''}`}
                aria-pressed={source === ''}
                onClick={() => {
                  clearPaginationForBaseRefresh()
                  setSource('')
                }}
              >
                Todas
              </button>
              {sources.map((item) => (
                <button
                  key={item}
                  type="button"
                  className={`ghost-button ${source === item ? 'is-active' : ''}`}
                  aria-pressed={source === item}
                  onClick={() => {
                    clearPaginationForBaseRefresh()
                    setSource(item)
                  }}
                >
                  {item}
                </button>
              ))}
            </div>
          )}

          <div className="skills-ref12__master-detail">
            <div className="skills-ref12__cards-column">
              <SkillCardsGrid
                state={list}
                items={displayedItems}
                isShowcaseMode={isShowcaseMode}
                selectedSkillId={selectedSkill.id}
                availabilityBySkill={availabilityBySkill}
                onSelectSkill={handleSelectSkill}
                onRetry={() => {
                  clearPaginationForBaseRefresh()
                  setReload((value) => value + 1)
                }}
                onLoadMore={() => void loadMore()}
                loadingMore={loadingMore}
                moreError={moreError}
                embedded={embedded}
              />
            </div>

            <aside
              className="skills-inspector"
              data-testid="skills-inspector-panel"
              aria-label="Detalhes da habilidade"
            >
              <div className="skills-inspector__header">
                <span className="skill-card-v6__icon" aria-hidden="true">
                  {selectedSkill.icon}
                </span>
                <div>
                  <h3 className="skills-inspector__title">
                    {selectedSkill.isShowcase
                      ? selectedSkill.name
                      : `${selectedSkill.name} · v${selectedSkill.version}`}
                  </h3>
                  <span
                    className={`skill-pill ${selectedSkill.isCustom ? 'is-custom' : 'is-installed'}`}
                  >
                    {selectedSkill.isCustom ? 'Criada por você' : 'Instalada'}
                  </span>
                </div>
              </div>

              <p className="skills-inspector__desc">
                {selectedSkill.isShowcase
                  ? selectedSkill.description
                  : `${selectedSkill.description} (${selectedSkill.source})`}
              </p>

              <div className="skills-inspector__settings">
                <div className="skills-inspector__setting-row">
                  <label htmlFor="skill-availability-select">Disponível em</label>
                  <select
                    id="skill-availability-select"
                    aria-label="Disponível em"
                    data-testid="skill-availability-select"
                    value={currentAvailability}
                    onChange={(event) =>
                      setAvailabilityBySkill((prev) => ({
                        ...prev,
                        [selectedSkill.id]: event.target.value as AvailabilityOption,
                      }))
                    }
                  >
                    <option value="Todos os projetos">Todos os projetos</option>
                    <option value="Apenas Auryly">Apenas Auryly</option>
                  </select>
                </div>

                <div className="skills-inspector__setting-row">
                  <label htmlFor="skill-confirm-toggle">
                    <span>Pedir confirmação · Antes de publicar</span>
                  </label>
                  <input
                    id="skill-confirm-toggle"
                    type="checkbox"
                    aria-label="Pedir confirmação antes de publicar"
                    checked={currentConfirm}
                    onChange={(event) =>
                      setConfirmBySkill((prev) => ({
                        ...prev,
                        [selectedSkill.id]: event.target.checked,
                      }))
                    }
                  />
                </div>
              </div>

              <div className="skill-example-card" data-testid="skill-quick-example">
                <span className="skill-example-card__label">Exemplo rápido</span>
                <p className="skill-example-card__prompt">“{selectedSkill.quickExample}”</p>
                <button type="button" className="skill-example-card__btn">
                  Testar no chat
                </button>
              </div>

              <div
                className="skill-instructions-accordion"
                data-testid="skill-instructions-accordion"
              >
                <button
                  type="button"
                  className="skill-instructions-accordion__trigger"
                  aria-expanded={instructionsOpen}
                  onClick={() => setInstructionsOpen((prev) => !prev)}
                >
                  <span>Instruções da habilidade</span>
                  <span aria-hidden="true">{instructionsOpen ? '▴' : '▾'}</span>
                </button>
                {instructionsOpen && (
                  <div className="skill-instructions-accordion__body">
                    <pre className="skills-instructions">{selectedSkill.instructions}</pre>
                  </div>
                )}
              </div>
            </aside>
          </div>

          <AgentSkillsPanel
            client={client}
            skills={list.status === 'loaded' ? list.value.items : []}
            availableSkills={list.status === 'loaded' ? list.value.items : []}
          />
        </>
      )}
    </section>
  )
  if (embedded) return content
  return (
    <main className="app-shell skills-shell">
      <header className="topbar">
        <Brand to="/" />
        <span className="topbar__context">Biblioteca de skills</span>
        <CommandPalette />
      </header>

      {content}
    </main>
  )
}

function SkillCardsGrid({
  state,
  items,
  isShowcaseMode,
  selectedSkillId,
  availabilityBySkill,
  onSelectSkill,
  onRetry,
  onLoadMore,
  loadingMore,
  moreError,
  embedded = false,
}: {
  state: ListState
  items: ReadonlyArray<InspectorSkillItem>
  isShowcaseMode: boolean
  selectedSkillId: string
  availabilityBySkill: Record<string, AvailabilityOption>
  onSelectSkill: (id: string) => void
  onRetry: () => void
  onLoadMore: () => void
  loadingMore: boolean
  moreError: boolean
  embedded?: boolean
}) {
  if (state.status === 'loading') {
    return (
      <p className="skills-state" role="status">
        Carregando skills…
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <div className="skills-state skills-state--error" role="alert">
        Não foi possível carregar as skills.
        <button type="button" className="button button--secondary" onClick={onRetry}>
          Tentar novamente
        </button>
      </div>
    )
  }
  if (!isShowcaseMode && state.value.items.length === 0) {
    return <p className="skills-state">Nenhuma skill corresponde à busca atual.</p>
  }

  return (
    <>
      <div className="skills-cards-grid skills-list" aria-label="Skills instaladas">
        {items.map((skill) => {
          const isSelected = skill.id === selectedSkillId
          const availability = availabilityBySkill[skill.id] ?? skill.defaultAvailability
          return (
            <article
              key={skill.id}
              className={`skill-card-v6${isSelected ? ' is-selected' : ''}`}
              onClick={() => onSelectSkill(skill.id)}
            >
              <div className="skill-card-v6__top">
                <span className="skill-card-v6__icon" aria-hidden="true">
                  {skill.icon}
                </span>
                <span className={`skill-pill ${skill.isCustom ? 'is-custom' : 'is-installed'}`}>
                  {skill.isCustom ? 'Criada por você' : 'Instalada'}
                </span>
              </div>

              <div className="skill-card-v6__title">
                {skill.isShowcase ? (
                  <strong>{skill.name}</strong>
                ) : (
                  <Link
                    className="skill-card-v6__title-link"
                    to={`${embedded ? '/settings/skills' : '/skills'}/${encodeURIComponent(skill.id)}`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    {skill.name}
                  </Link>
                )}
              </div>

              <p className="skill-card-v6__desc">{skill.description}</p>

              <div className="skill-card-v6__footer">
                <span>
                  {availability === 'Apenas Auryly'
                    ? 'Somente no projeto Auryly'
                    : 'Disponível em todos os projetos'}
                </span>
                {!skill.isShowcase && (
                  <span className="skill-row__meta">
                    <code>v{skill.version}</code>
                    <span>{skill.source}</span>
                    <span
                      aria-label={skill.available ? 'Disponível' : 'Indisponível'}
                      className={
                        skill.available
                          ? 'skill-row__availability is-available'
                          : 'skill-row__availability'
                      }
                    >
                      {skill.available ? 'Disponível' : 'Indisponível'}
                    </span>
                  </span>
                )}
              </div>
            </article>
          )
        })}
      </div>
      {state.value.next_cursor && (
        <button
          type="button"
          className="button button--secondary skills-load-more"
          onClick={onLoadMore}
          disabled={loadingMore}
        >
          {loadingMore ? 'Carregando…' : 'Carregar mais skills'}
        </button>
      )}
      {moreError && (
        <div className="skills-more-error" role="alert">
          Não foi possível carregar mais skills.
          <button type="button" className="button button--secondary" onClick={onLoadMore}>
            Tentar carregar mais
          </button>
        </div>
      )}
    </>
  )
}

export function AgentSkillsPanel({
  client,
  skills,
  availableSkills: availableSkillsProp,
}: {
  client: SkillsClient
  skills?: SkillSummary[]
  availableSkills?: SkillSummary[]
}) {
  const inputSkills = useMemo(() => skills ?? availableSkillsProp ?? [], [availableSkillsProp, skills])
  const [agentId, setAgentId] = useState('agent:main')
  const [mode, setMode] = useState<AgentSkillMode>('auto')
  const [pinned, setPinned] = useState<string[]>([])
  const [associationItems, setAssociationItems] = useState<SkillSummary[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'loaded' | 'saving' | 'error'>('idle')
  const [error, setError] = useState('')
  const selected = new Set(pinned)

  const availableSkills = useMemo(() => {
    const byId = new Map(associationItems.map((skill) => [skill.id, skill]))
    inputSkills.forEach((skill) => byId.set(skill.id, skill))
    return [...byId.values()]
  }, [associationItems, inputSkills])
  const associatedSkills = availableSkills.filter((skill) => selected.has(skill.id))

  async function load() {
    if (!agentId.trim() || status === 'loading' || status === 'saving') return
    setStatus('loading')
    setError('')
    try {
      const association = await client.getAgentSkills(agentId.trim())
      setMode(association.mode)
      setPinned(association.items.map((item) => item.id))
      setAssociationItems(association.items)
      setStatus('loaded')
    } catch {
      setError('Não foi possível carregar as skills deste agente.')
      setStatus('error')
    }
  }

  async function save() {
    if (!agentId.trim() || status !== 'loaded') return
    setStatus('saving')
    setError('')
    try {
      const association = await client.setAgentSkills(agentId.trim(), {
        mode,
        skill_ids: mode === 'pinned' ? pinned : [],
      })
      setMode(association.mode)
      setPinned(association.items.map((item) => item.id))
      setAssociationItems(association.items)
      setStatus('loaded')
    } catch {
      setError('Não foi possível salvar as skills deste agente.')
      setStatus('loaded')
    }
  }

  function togglePinned(skillId: string) {
    setPinned((current) =>
      current.includes(skillId) ? current.filter((item) => item !== skillId) : [...current, skillId],
    )
  }

  return (
    <section className="agent-skills" aria-labelledby="agent-skills-title">
      <div>
        <p className="eyebrow">CONFIGURAÇÃO DO AGENTE</p>
        <h2 id="agent-skills-title">Skills do agente</h2>
        <p>Defina se o agente descobre skills automaticamente ou trabalha com uma seleção fixada.</p>
      </div>
      <div className="agent-skills__load">
        <label>
          ID do agente
          <input value={agentId} onChange={(event) => setAgentId(event.target.value)} />
        </label>
        <button
          type="button"
          className="button button--secondary"
          onClick={() => void load()}
          disabled={status === 'loading' || status === 'saving'}
        >
          {status === 'loading' ? 'Carregando…' : 'Carregar configuração'}
        </button>
      </div>
      {status === 'loaded' || status === 'saving' ? (
        <>
          <fieldset className="agent-skills__mode">
            <legend>Modo de descoberta</legend>
            <label>
              <input
                type="radio"
                name="agent-skill-mode"
                checked={mode === 'auto'}
                onChange={() => setMode('auto')}
              />
              Auto discover
            </label>
            <label>
              <input
                type="radio"
                name="agent-skill-mode"
                checked={mode === 'pinned'}
                onChange={() => setMode('pinned')}
              />
              Skills fixadas
            </label>
          </fieldset>
          <div className="agent-skills__pinned" aria-disabled={mode !== 'pinned'}>
            <p>Skills fixadas</p>
            {associatedSkills.length > 0 && (
              <ul aria-label="Skills fixadas">
                {associatedSkills.map((skill) => (
                  <li key={skill.id}>{skill.name}</li>
                ))}
              </ul>
            )}
            <div className="agent-skills__picker">
              {availableSkills.map((skill) => (
                <label key={skill.id}>
                  <input
                    type="checkbox"
                    checked={selected.has(skill.id)}
                    disabled={mode !== 'pinned'}
                    onChange={() => togglePinned(skill.id)}
                  />
                  {skill.name}
                </label>
              ))}
            </div>
          </div>
          <button
            type="button"
            className="button button--primary"
            onClick={() => void save()}
            disabled={status === 'saving'}
          >
            {status === 'saving' ? 'Salvando…' : 'Salvar configuração'}
          </button>
        </>
      ) : (
        <p className="agent-skills__hint">Carregue uma configuração para editar as skills fixadas.</p>
      )}
      {error && (
        <p className="agent-skills__error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

function SkillDetailPage({
  client,
  skillId,
  embedded = false,
}: {
  client: SkillsClient
  skillId: string
  embedded?: boolean
}) {
  const [detail, setDetail] = useState<DetailState>({ status: 'loading' })
  const [reload, setReload] = useState(0)
  const [editing, setEditing] = useState(false)
  const [usedBy, setUsedBy] = useState<SkillAgent[]>([])
  const [removingVersion, setRemovingVersion] = useState<string | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    client
      .get(skillId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDetail({ status: 'loaded', value })
      })
      .catch(() => {
        if (!controller.signal.aborted) setDetail({ status: 'error' })
      })
    return () => controller.abort()
  }, [client, reload, skillId])

  useEffect(() => {
    const controller = new AbortController()
    client
      .listSkillAgents(skillId, controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setUsedBy(items)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [client, skillId])

  async function removeVersion(version: string) {
    if (removingVersion || detail.status !== 'loaded' || version === detail.value.version) return
    if (!window.confirm(`Desinstalar a versão ${version}? Ela será removida desta instalação.`)) return
    setRemovingVersion(version)
    setRemoveError(null)
    try {
      const next = await client.removeVersion(skillId, version)
      setDetail({ status: 'loaded', value: next })
    } catch {
      setRemoveError('Não foi possível desinstalar essa versão. Ela pode estar sendo usada por um agente.')
    } finally {
      setRemovingVersion(null)
    }
  }

  const content = (
    <section className="skills-detail" aria-live="polite">
      {detail.status === 'loading' && (
        <p className="skills-state" role="status">
          Carregando skill…
        </p>
      )}
      {detail.status === 'error' && (
        <div className="skills-state skills-state--error" role="alert">
          Não foi possível carregar esta skill.
          <button
            type="button"
            className="button button--secondary"
            onClick={() => setReload((value) => value + 1)}
          >
            Tentar novamente
          </button>
        </div>
      )}
      {detail.status === 'loaded' &&
        (editing ? (
          <EditSkillForm
            client={client}
            skill={detail.value}
            onCancel={() => setEditing(false)}
            onUpdated={(value) => {
              setDetail({ status: 'loaded', value })
              setEditing(false)
            }}
          />
        ) : (
          <SkillDetailView
            skill={detail.value}
            usedBy={usedBy}
            onEdit={() => setEditing(true)}
            onRemoveVersion={(version) => void removeVersion(version)}
            removingVersion={removingVersion}
            removeError={removeError}
          />
        ))}
    </section>
  )
  if (embedded) return content
  return (
    <main className="app-shell skills-shell">
      <header className="topbar">
        <Brand to="/" />
        <span className="topbar__context">Detalhe da skill</span>
        <Link className="topbar__back" to="/skills">
          Voltar às skills
        </Link>
      </header>
      {content}
    </main>
  )
}

function SkillDetailView({
  skill,
  usedBy,
  onEdit,
  onRemoveVersion,
  removingVersion,
  removeError,
}: {
  skill: SkillDetail
  usedBy: SkillAgent[]
  onEdit: () => void
  onRemoveVersion: (version: string) => void
  removingVersion: string | null
  removeError: string | null
}) {
  return (
    <>
      <p className="eyebrow">
        {skill.source} / {skill.available ? 'DISPONÍVEL' : 'INDISPONÍVEL'}
      </p>
      <h1>{skill.name}</h1>
      <div className="skills-detail__summary">
        <p className="skills-detail__description">{skill.description}</p>
        <button type="button" className="button button--secondary" onClick={onEdit}>
          Editar skill
        </button>
      </div>
      <dl className="skills-detail__facts">
        <div>
          <dt>Versão</dt>
          <dd>
            <code>{skill.version}</code>
          </dd>
        </div>
        <div>
          <dt>Tags</dt>
          <dd>
            {skill.tags.length
              ? skill.tags.map((tag) => (
                  <span key={tag} className="skill-tag">
                    {tag}
                  </span>
                ))
              : 'Sem tags'}
          </dd>
        </div>
      </dl>
      <div className="skills-detail__disclosures">
        <Disclosure label="Instruções">
          <pre className="skills-instructions">{skill.instructions}</pre>
        </Disclosure>
        <Disclosure label="Dependências">
          <MetadataList items={skill.dependencies} empty="Sem dependências." />
        </Disclosure>
        <Disclosure label="Uso e ferramentas">
          <MetadataList items={skill.requires_tools} empty="Sem ferramentas obrigatórias." />
        </Disclosure>
        <Disclosure label={`Versões instaladas · ${skill.versions.length}`}>
          <div className="skill-versions" aria-label="Versões instaladas">
            {skill.versions.map((version) => {
              const current = version === skill.version
              return (
                <div className={`skill-version-row${current ? ' is-current' : ''}`} key={version}>
                  <div className="skill-version-row__identity">
                    <span className="skill-version-row__dot" aria-hidden="true" />
                    <code>v{version}</code>
                    {current && <span className="skill-version-row__badge">Atual</span>}
                  </div>
                  {current ? (
                    <span className="skill-version-row__protected">Em uso</span>
                  ) : skill.source === 'custom' ? (
                    <button
                      type="button"
                      className="skill-version-row__remove"
                      aria-label={`Desinstalar versão ${version}`}
                      onClick={() => onRemoveVersion(version)}
                      disabled={removingVersion !== null}
                    >
                      {removingVersion === version ? 'Desinstalando…' : 'Desinstalar'}
                    </button>
                  ) : (
                    <span className="skill-version-row__protected">Protegida</span>
                  )}
                </div>
              )
            })}
          </div>
          {skill.versions.length < 2 && (
            <p className="skills-metadata__empty">Não há versões antigas instaladas.</p>
          )}
          {removeError && (
            <p className="skills-form__error" role="alert">
              {removeError}
            </p>
          )}
        </Disclosure>
        {usedBy.length > 0 && (
          <Disclosure label="Usada por">
            <ul className="skills-metadata">
              {usedBy.map((agent) => (
                <li key={agent.agent_id}>
                  {agent.agent_id} · {agent.mode}
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
      </div>
    </>
  )
}

function MetadataList({ items, empty }: { items: string[]; empty: string }) {
  return items.length ? (
    <ul className="skills-metadata">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  ) : (
    <p className="skills-metadata__empty">{empty}</p>
  )
}

function CreateSkillForm({
  client,
  onCancel,
  onCreated,
}: {
  client: SkillsClient
  onCancel: () => void
  onCreated: (created: Awaited<ReturnType<SkillsClient['create']>>) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [tags, setTags] = useState('')
  const [instructions, setInstructions] = useState('')
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input: CreateSkillInput = {
      name: name.trim(),
      description: description.trim(),
      version: version.trim(),
      tags: tags
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      instructions: instructions.trim(),
    }
    if (saving || !input.name || !input.description || !input.version || !input.instructions) return
    setSaving(true)
    setFailed(false)
    try {
      onCreated(await client.create(input))
    } catch {
      setFailed(true)
      setSaving(false)
    }
  }

  return (
    <form className="skills-form" onSubmit={submit}>
      <div className="skills-form__heading">
        <div>
          <p className="eyebrow">NOVA SKILL</p>
          <h2>Defina o procedimento</h2>
        </div>
        <button type="button" className="button button--quiet" onClick={onCancel} disabled={saving}>
          Cancelar
        </button>
      </div>
      <label>
        Nome
        <input required value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        Descrição
        <textarea required value={description} onChange={(event) => setDescription(event.target.value)} />
      </label>
      <label>
        Instruções
        <textarea
          required
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          placeholder="Descreva o procedimento em Markdown"
        />
      </label>
      <label>
        Tags
        <input
          value={tags}
          onChange={(event) => setTags(event.target.value)}
          placeholder="debugging, testing"
        />
      </label>
      <Disclosure label="Campos avançados" defaultOpen={false} className="skills-form__advanced">
        <label>
          Versão
          <input required value={version} onChange={(event) => setVersion(event.target.value)} />
        </label>
      </Disclosure>
      {failed && (
        <p role="alert" className="skills-form__error">
          Não foi possível salvar a skill. Revise os campos e tente novamente.
        </p>
      )}
      <div className="skills-form__actions">
        <button type="submit" className="button button--primary" disabled={saving}>
          {saving ? 'Salvando…' : 'Salvar skill'}
        </button>
      </div>
    </form>
  )
}

function EditSkillForm({
  client,
  skill,
  onCancel,
  onUpdated,
}: {
  client: SkillsClient
  skill: SkillDetail
  onCancel: () => void
  onUpdated: (skill: SkillDetail) => void
}) {
  const [name, setName] = useState(skill.name)
  const [description, setDescription] = useState(skill.description)
  const [version, setVersion] = useState(skill.version)
  const [tags, setTags] = useState(skill.tags.join(', '))
  const [instructions, setInstructions] = useState(skill.instructions)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input: CreateSkillInput = {
      name: name.trim(),
      description: description.trim(),
      version: version.trim(),
      tags: tags
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      instructions: instructions.trim(),
    }
    if (saving || !input.name || !input.description || !input.version || !input.instructions) return
    setSaving(true)
    setFailed(false)
    try {
      onUpdated(await client.update(skill.id, input))
    } catch {
      setFailed(true)
      setSaving(false)
    }
  }

  return (
    <form className="skills-form" onSubmit={submit}>
      <div className="skills-form__heading">
        <div>
          <p className="eyebrow">EDITAR SKILL</p>
          <h2>Atualize o procedimento</h2>
        </div>
        <button type="button" className="button button--quiet" onClick={onCancel} disabled={saving}>
          Cancelar
        </button>
      </div>
      <label>
        Nome
        <input required value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        Descrição
        <textarea required value={description} onChange={(event) => setDescription(event.target.value)} />
      </label>
      <label>
        Instruções
        <textarea
          required
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
        />
      </label>
      <label>
        Tags
        <input
          value={tags}
          onChange={(event) => setTags(event.target.value)}
          placeholder="debugging, testing"
        />
      </label>
      <Disclosure label="Campos avançados" className="skills-form__advanced">
        <label>
          Versão
          <input required value={version} onChange={(event) => setVersion(event.target.value)} />
        </label>
      </Disclosure>
      {failed && (
        <p role="alert" className="skills-form__error">
          Não foi possível salvar as alterações. Revise os campos e tente novamente.
        </p>
      )}
      <div className="skills-form__actions">
        <button type="submit" className="button button--primary" disabled={saving}>
          {saving ? 'Salvando…' : 'Salvar alterações'}
        </button>
      </div>
    </form>
  )
}
