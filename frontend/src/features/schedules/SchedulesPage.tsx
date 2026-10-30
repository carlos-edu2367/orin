import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { createBrowserApiClient } from '../../api/client'
import { listProjects, type Project } from '../../api/projects'
import { listProviderModels, type ProviderName } from '../../api/providers'
import {
  cancelScheduledChat,
  createScheduledChat,
  formatHumanCron,
  formatHumanWhen,
  listScheduledChats,
  type ScheduleRecurrence,
  type ScheduledChat,
} from '../../api/schedules'
import { Brand } from '../../components/Brand'

const DEFAULT_TIMEZONE = 'America/Sao_Paulo'
const TIMEZONE_OPTIONS = [
  ['America/Sao_Paulo', 'São Paulo (UTC−03:00)'],
  ['America/New_York', 'Nova York'],
  ['America/Los_Angeles', 'Los Angeles'],
  ['America/Mexico_City', 'Cidade do México'],
  ['UTC', 'UTC'],
  ['Europe/Lisbon', 'Lisboa'],
  ['Europe/London', 'Londres'],
  ['Europe/Paris', 'Paris'],
  ['Asia/Tokyo', 'Tóquio'],
  ['Australia/Sydney', 'Sydney'],
] as const

type ScheduleTab = 'active' | 'paused' | 'completed'
type RecurrenceMode = 'once' | 'repeat'
type FrequencyPreset = 'weekdays' | 'friday' | 'every30m' | 'daily' | 'custom'

type ShowcaseScheduleRow = {
  id: string
  title: string
  subtitle: string
  cron: string
  project: string
  statusLabel: 'Ativa' | 'Monitorando' | 'Pausada' | 'Concluída'
  statusClass: 'is-active' | 'is-monitoring' | 'is-paused' | 'is-completed'
  tab: ScheduleTab
}

const SHOWCASE_ROWS: ShowcaseScheduleRow[] = [
  {
    id: 'demo-sales',
    title: 'Resumo diário das vendas',
    subtitle: 'Enviado no chat e por voz',
    cron: '0 9 * * 1-5',
    project: 'Auryly',
    statusLabel: 'Ativa',
    statusClass: 'is-active',
    tab: 'active',
  },
  {
    id: 'demo-weekly',
    title: 'Relatório semanal da Auryly',
    subtitle: 'Gera PDF e salva no projeto',
    cron: '0 18 * * 5',
    project: 'Auryly',
    statusLabel: 'Ativa',
    statusClass: 'is-active',
    tab: 'active',
  },
  {
    id: 'demo-uptime',
    title: 'Checar site no ar',
    subtitle: 'Avisa apenas se encontrar erro',
    cron: '*/30 * * * *',
    project: 'Orin Core',
    statusLabel: 'Monitorando',
    statusClass: 'is-monitoring',
    tab: 'active',
  },
]

function buildCronExpression(preset: FrequencyPreset, time: string, customCron: string): string {
  if (preset === 'every30m') return '*/30 * * * *'
  if (preset === 'custom') {
    const cleaned = customCron.replace(/[\r\n\t]+/g, ' ').trim().slice(0, 120)
    return cleaned || '0 9 * * 1-5'
  }
  const [rawHour = '09', rawMin = '00'] = time.split(':')
  const parsedHour = Number(rawHour)
  const parsedMin = Number(rawMin)
  const hour = Number.isNaN(parsedHour) || parsedHour < 0 || parsedHour > 23 ? 9 : parsedHour
  const minute = Number.isNaN(parsedMin) || parsedMin < 0 || parsedMin > 59 ? 0 : parsedMin
  if (preset === 'friday') return `${minute} ${hour} * * 5`
  if (preset === 'daily') return `${minute} ${hour} * * *`
  return `${minute} ${hour} * * 1-5`
}

export function SchedulesPage({ embedded = false }: { embedded?: boolean }) {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [items, setItems] = useState<ScheduledChat[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [activeTab, setActiveTab] = useState<ScheduleTab>('active')
  const [message, setMessage] = useState('')
  const [provider] = useState<ProviderName>('openrouter')
  const [modelId, setModelId] = useState('model-1')
  const [projectId, setProjectId] = useState('')
  const [mode, setMode] = useState<RecurrenceMode>('repeat')
  const [frequencyPreset, setFrequencyPreset] = useState<FrequencyPreset>('weekdays')
  const [customCron, setCustomCron] = useState('0 9 * * 1-5')
  const [fireAt, setFireAt] = useState('')
  const [time, setTime] = useState('09:00')
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE)
  const [error, setError] = useState<string | null>(null)
  const [testFeedback, setTestFeedback] = useState<string | null>(null)

  const reload = useCallback(
    () =>
      Promise.all([listScheduledChats(client), listProjects(client)]).then(([schedules, availableProjects]) => {
        setItems(schedules.items)
        setProjects(availableProjects.items)
      }),
    [client],
  )

  useEffect(() => {
    let active = true
    void reload().catch(() => {
      if (active) setError('Não foi possível carregar as agendas.')
    })
    return () => {
      active = false
    }
  }, [reload])

  useEffect(() => {
    const controller = new AbortController()
    void listProviderModels(client, provider, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted && value.length > 0) {
          setModelId(value.find((item) => item.is_favorite)?.model_id ?? value[0]?.model_id ?? 'model-1')
        }
      })
      .catch(() => {
        // Keep default fallback modelId when models endpoint is unavailable
      })
    return () => controller.abort()
  }, [client, provider])

  function handleFrequencyChange(value: FrequencyPreset) {
    setFrequencyPreset(value)
    if (value === 'friday' && time === '09:00') {
      setTime('18:00')
    }
  }

  function handleTestNow() {
    const promptPreview = message.trim() || 'Resumo diário das vendas'
    const cronExpr = buildCronExpression(frequencyPreset, time, customCron)
    const scheduleDesc = mode === 'once' && fireAt ? `Uma vez (${fireAt})` : formatHumanCron(cronExpr)
    setTestFeedback(`Simulação pronta: "${promptPreview}" · ${scheduleDesc} (${timezone})`)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const trimmed = message.trim()
    if (!trimmed || !modelId) return

    const effectiveMode: RecurrenceMode = mode === 'once' || fireAt.trim() !== '' ? 'once' : 'repeat'
    if (effectiveMode === 'once' && !fireAt.trim()) return

    const cronExpression = buildCronExpression(frequencyPreset, time, customCron)
    const recurrence: ScheduleRecurrence =
      effectiveMode === 'once'
        ? { kind: 'once', fire_at: fireAt }
        : { kind: 'cron', expression: cronExpression }

    try {
      await createScheduledChat(client, {
        message: trimmed,
        provider,
        model_id: modelId,
        timezone,
        recurrence,
        project_id: projectId || null,
      })
      setMessage('')
      setFireAt('')
      await reload()
    } catch {
      setError('Não foi possível salvar a agenda. Confira a data, modelo e provider.')
    }
  }

  async function cancel(scheduleId: string) {
    try {
      await cancelScheduledChat(client, scheduleId)
      await reload()
    } catch {
      setError('Não foi possível cancelar a agenda.')
    }
  }

  const activeApiItems = items.filter((item) => item.state === 'ACTIVE')
  const pausedApiItems = items.filter((item) => item.state === 'PAUSED')
  const completedApiItems = items.filter((item) => item.state !== 'ACTIVE' && item.state !== 'PAUSED')

  const isShowcase = items.length === 0
  const activeCount = isShowcase ? SHOWCASE_ROWS.length : activeApiItems.length
  const pausedCount = pausedApiItems.length
  const completedCount = completedApiItems.length

  const visibleApiItems =
    activeTab === 'active'
      ? [...activeApiItems, ...completedApiItems]
      : activeTab === 'paused'
        ? pausedApiItems
        : completedApiItems

  const visibleShowcaseRows = isShowcase ? SHOWCASE_ROWS.filter((row) => row.tab === activeTab) : []

  const content = (
    <section className="schedules-page schedules-ref09__surface" aria-labelledby="schedules-title">
      <header className="schedules-ref09__header">
        <div className="schedules-ref09__header-copy">
          {embedded ? (
            <>
              <h1 className="visually-hidden">Agendamentos</h1>
              <h2 id="schedules-title" className="schedules-ref09__title" data-testid="schedules-heading">
                Ações agendadas
              </h2>
            </>
          ) : (
            <h1 id="schedules-title" className="schedules-ref09__title" data-testid="schedules-heading">
              Ações agendadas
            </h1>
          )}
          <p className="schedules-ref09__subtitle">
            Tarefas que o Orin executa sozinho nos horários combinados.
          </p>
        </div>
        <button
          type="button"
          className="schedules-ref09__new-btn"
          data-testid="schedules-new-action-button"
          onClick={() => document.getElementById('schedule-prompt')?.focus()}
        >
          + Nova ação
        </button>
      </header>

      <div className="schedules-ref09__toolbar">
        <div className="schedules-ref09__tabs" role="tablist" aria-label="Filtro de ações agendadas">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'active'}
            className={`schedules-ref09__tab${activeTab === 'active' ? ' is-active' : ''}`}
            data-testid="schedules-tab-active"
            onClick={() => setActiveTab('active')}
          >
            Ativas <span>{activeCount}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'paused'}
            className={`schedules-ref09__tab${activeTab === 'paused' ? ' is-active' : ''}`}
            data-testid="schedules-tab-paused"
            onClick={() => setActiveTab('paused')}
          >
            Pausadas <span>{pausedCount}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'completed'}
            className={`schedules-ref09__tab${activeTab === 'completed' ? ' is-active' : ''}`}
            data-testid="schedules-tab-completed"
            onClick={() => setActiveTab('completed')}
          >
            Concluídas <span>{completedCount}</span>
          </button>
        </div>
      </div>

      {error && (
        <p className="schedules-page__error" role="alert">
          {error}
        </p>
      )}

      <div className="schedules-ref09__master-detail">
        <div className="schedules-ref09__table-card">
          <table className="schedules-table" data-testid="schedules-table">
            <thead>
              <tr>
                <th scope="col">AÇÃO</th>
                <th scope="col">QUANDO</th>
                <th scope="col">PROJETO</th>
                <th scope="col">STATUS</th>
              </tr>
            </thead>
            <tbody>
              {isShowcase ? (
                visibleShowcaseRows.length > 0 ? (
                  visibleShowcaseRows.map((row) => (
                    <tr key={row.id} className="schedules-table__row">
                      <td>
                        <strong className="schedules-table__action-title">{row.title}</strong>
                        <span className="schedules-table__action-sub">{row.subtitle}</span>
                      </td>
                      <td>
                        <span className="schedules-table__when-main">{formatHumanCron(row.cron)}</span>
                        <span className="schedules-table__cron-meta">cron {row.cron}</span>
                      </td>
                      <td>
                        <span className="schedules-table__project-pill">{row.project}</span>
                      </td>
                      <td>
                        <span className={`schedules-status-pill ${row.statusClass}`}>{row.statusLabel}</span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr className="schedules-table__row">
                    <td colSpan={4}>
                      <span className="schedules-table__action-sub">Nenhuma ação neste filtro.</span>
                    </td>
                  </tr>
                )
              ) : visibleApiItems.length > 0 ? (
                visibleApiItems.map((item) => (
                  <ApiScheduleTableRow
                    key={item.schedule_id}
                    item={item}
                    projects={projects}
                    onCancel={cancel}
                  />
                ))
              ) : (
                <tr className="schedules-table__row">
                  <td colSpan={4}>
                    <span className="schedules-table__action-sub">Nenhuma ação neste filtro.</span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <aside className="schedules-drawer" data-testid="schedules-drawer" aria-label="Nova ação">
          <header className="schedules-drawer__header">
            <h3>Nova ação</h3>
          </header>

          <form className="schedules-drawer__form" onSubmit={(event) => void submit(event)}>
            <div className="schedule-recurrence-cards" role="group" aria-label="Modo de recorrência">
              <button
                type="button"
                className={`schedule-recurrence-card${mode === 'once' ? ' is-selected' : ''}`}
                data-testid="schedule-mode-once"
                aria-pressed={mode === 'once'}
                onClick={() => setMode('once')}
              >
                <strong>Uma vez</strong>
                <span>Data específica</span>
              </button>
              <button
                type="button"
                className={`schedule-recurrence-card${mode === 'repeat' ? ' is-selected' : ''}`}
                data-testid="schedule-mode-repeat"
                aria-pressed={mode === 'repeat'}
                onClick={() => {
                  setMode('repeat')
                  setFireAt('')
                }}
              >
                <strong>Repetir</strong>
                <span>Rotina recorrente</span>
              </button>
            </div>

            <label className="schedules-drawer__field" htmlFor="schedule-prompt">
              <span>O que o Orin deve fazer?</span>
              <textarea
                id="schedule-prompt"
                aria-label="Instrução"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                placeholder="Ex.: revise as tarefas abertas do projeto e me traga um resumo"
                rows={3}
              />
            </label>

            <div className="schedules-drawer__row-2col">
              <label className="schedules-drawer__field" htmlFor="schedule-frequency">
                <span>Frequência</span>
                <select
                  id="schedule-frequency"
                  aria-label="Frequência"
                  value={frequencyPreset}
                  onChange={(event) => handleFrequencyChange(event.target.value as FrequencyPreset)}
                >
                  <option value="weekdays">Segunda a sexta</option>
                  <option value="friday">Toda sexta</option>
                  <option value="every30m">A cada 30 minutos</option>
                  <option value="daily">Todos os dias</option>
                  <option value="custom">Personalizado (cron)</option>
                </select>
              </label>

              <label className="schedules-drawer__field" htmlFor="schedule-time">
                <span>Horário</span>
                <input
                  id="schedule-time"
                  type="time"
                  aria-label="Horário"
                  value={time}
                  onChange={(event) => setTime(event.target.value)}
                />
              </label>
            </div>

            {frequencyPreset === 'custom' && (
              <label className="schedules-drawer__field" htmlFor="schedule-custom-cron">
                <span>Expressão cron</span>
                <input
                  id="schedule-custom-cron"
                  type="text"
                  aria-label="Expressão cron"
                  value={customCron}
                  onChange={(event) => setCustomCron(event.target.value)}
                />
              </label>
            )}

            <div className="schedules-drawer__row-2col">
              <label className="schedules-drawer__field" htmlFor="schedule-timezone">
                <span>Fuso horário</span>
                <select
                  id="schedule-timezone"
                  aria-label="Fuso horário"
                  value={timezone}
                  onChange={(event) => setTimezone(event.target.value)}
                >
                  {TIMEZONE_OPTIONS.map(([value, labelText]) => (
                    <option key={value} value={value}>
                      {labelText}
                    </option>
                  ))}
                </select>
              </label>

              <label className="schedules-drawer__field" htmlFor="schedule-fire-at">
                <span>Data e hora</span>
                <input
                  id="schedule-fire-at"
                  type="datetime-local"
                  aria-label="Data e hora"
                  value={fireAt}
                  onChange={(event) => {
                    const value = event.target.value
                    setFireAt(value)
                    if (value) setMode('once')
                  }}
                />
              </label>
            </div>

            {projects.length > 0 && (
              <label className="schedules-drawer__field" htmlFor="schedule-project">
                <span>Projeto</span>
                <select
                  id="schedule-project"
                  aria-label="Projeto"
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                >
                  <option value="">Geral</option>
                  {projects.map((project) => (
                    <option key={project.project_id} value={project.project_id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {testFeedback && (
              <div
                className="schedules-drawer__test-feedback"
                data-testid="schedule-test-feedback"
                role="status"
              >
                {testFeedback}
              </div>
            )}

            <div className="schedules-drawer__actions">
              <button
                type="button"
                className="schedules-drawer__test-btn"
                data-testid="schedule-test-now"
                onClick={handleTestNow}
              >
                Testar agora
              </button>
              <button
                type="submit"
                className="schedules-drawer__submit"
                data-testid="schedules-submit-button"
                aria-label="Criar tarefa"
              >
                Criar ação
              </button>
            </div>
          </form>
        </aside>
      </div>
    </section>
  )

  if (embedded) {
    return (
      <div className="hub-page hub-page--schedules schedules-ref09" data-testid="schedules-hub-page">
        {content}
      </div>
    )
  }

  return (
    <main className="app-shell schedules-shell schedules-ref09">
      <header className="topbar">
        <Brand to="/" />
        <span className="topbar__context">Automações</span>
        <Link className="topbar__back" to="/">
          Voltar ao chat
        </Link>
      </header>
      {content}
    </main>
  )
}

function ApiScheduleTableRow({
  item,
  projects,
  onCancel,
}: {
  item: ScheduledChat
  projects: Project[]
  onCancel: (scheduleId: string) => Promise<void>
}) {
  const conversationPath = item.conversation_id
    ? item.project_id
      ? `/projects/${item.project_id}/chats/${item.conversation_id}`
      : `/chats/${item.conversation_id}`
    : null

  const projectName = item.project_id
    ? projects.find((p) => p.project_id === item.project_id)?.name ?? item.project_id
    : 'Geral'

  const tz = item.timezone ?? DEFAULT_TIMEZONE

  return (
    <tr className="schedules-table__row">
      <td>
        <strong className="schedules-table__action-title">{item.message}</strong>
        <span className="schedules-table__action-sub">
          {item.byAgent && <span className="schedules-badge schedules-badge--agent">pelo agente</span>}
          <span>
            {item.provider} · {item.model_id}
          </span>
          {conversationPath && <Link to={conversationPath}>Abrir chat</Link>}
        </span>
      </td>
      <td>
        <span className="schedules-table__when-main">{formatHumanWhen(item)}</span>
        <span className="schedules-table__cron-meta">
          {item.cron ? `cron ${item.cron} · ${tz}` : formatNextFire(item.next_fire_at)}
        </span>
      </td>
      <td>
        <span className="schedules-table__project-pill">{projectName}</span>
      </td>
      <td>
        <span className={`schedules-status-pill ${statusPillClass(item.state)}`}>{stateLabel(item.state)}</span>
        {item.state === 'ACTIVE' && (
          <button
            type="button"
            className="schedules-table__cancel-btn"
            onClick={() => void onCancel(item.schedule_id)}
          >
            Cancelar
          </button>
        )}
      </td>
    </tr>
  )
}

function formatNextFire(value: string | null): string {
  if (!value) return 'sem próxima execução'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'data indisponível'
    : `próxima · ${date.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}`
}

function stateLabel(value: string): string {
  return (
    ({
      ACTIVE: 'Ativa',
      PAUSED: 'Pausada',
      COMPLETED: 'Concluída',
      EXPIRED: 'Concluída',
      CANCELLED: 'Cancelada',
    } as Record<string, string>)[value] ?? value
  )
}

function statusPillClass(value: string): string {
  if (value === 'ACTIVE') return 'is-active'
  if (value === 'PAUSED') return 'is-paused'
  return 'is-completed'
}
