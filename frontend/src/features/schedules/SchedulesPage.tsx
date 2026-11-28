import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { createBrowserApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { listProjects, type Project } from '../../api/projects'
import {
  listProviderModels,
  PROVIDER_NAMES,
  type ProviderModel,
  type ProviderName,
} from '../../api/providers'
import {
  createScheduledChat,
  deleteScheduledChat,
  formatHumanCron,
  formatHumanWhen,
  formatModelDisplayName,
  formatProviderShort,
  formatTimezoneShort,
  listScheduledChats,
  summarizeScheduleMessage,
  updateScheduledChat,
  type ScheduledChat,
  type ScheduleRecurrence,
} from '../../api/schedules'
import { Brand } from '../../components/Brand'
import { resetSettingsBadgeCache } from '../settings/useSettingsBadges'
import './schedules.css'

const DEFAULT_TIMEZONE = 'America/Sao_Paulo'
const BASE_TIMEZONE_OPTIONS: ReadonlyArray<readonly [string, string]> = [
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
]

type ScheduleTab = 'active' | 'paused' | 'completed'
type RecurrenceMode = 'once' | 'repeat'
type FrequencyPreset = 'daily' | 'weekdays' | 'friday' | 'every30m' | 'hourly' | 'custom'

function buildCronExpression(preset: FrequencyPreset, time: string, customCron: string): string {
  if (preset === 'every30m') return '*/30 * * * *'
  if (preset === 'hourly') return '0 * * * *'
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

function parseScheduleIntoForm(item: ScheduledChat): {
  mode: RecurrenceMode
  frequencyPreset: FrequencyPreset
  time: string
  customCron: string
  fireAt: string
} {
  if (item.recurrence === 'once') {
    return {
      mode: 'once',
      frequencyPreset: 'daily',
      time: '09:00',
      customCron: '0 9 * * *',
      fireAt: toDatetimeLocalValue(item.next_fire_at ?? item.fire_at ?? null),
    }
  }
  if (item.recurrence === 'hourly') {
    return { mode: 'repeat', frequencyPreset: 'hourly', time: '09:00', customCron: '0 * * * *', fireAt: '' }
  }
  if (item.recurrence === 'daily') {
    const time = item.time_of_day || '09:00'
    return { mode: 'repeat', frequencyPreset: 'daily', time, customCron: buildCronExpression('daily', time, ''), fireAt: '' }
  }
  if (item.recurrence === 'weekly') {
    const time = item.time_of_day || '09:00'
    if (item.weekday === 4) {
      return { mode: 'repeat', frequencyPreset: 'friday', time, customCron: buildCronExpression('friday', time, ''), fireAt: '' }
    }
    const [h = '09', m = '00'] = time.split(':')
    // Python weekday: 0=Mon..6=Sun -> Cron dow: 1=Mon..6=Sat, 0=Sun
    const cronDow = typeof item.weekday === 'number' ? ((item.weekday + 1) % 7) : 1
    return { mode: 'repeat', frequencyPreset: 'custom', time, customCron: `${Number(m)} ${Number(h)} * * ${cronDow}`, fireAt: '' }
  }
  const cron = (item.cron ?? '').trim()
  if (cron === '*/30 * * * *') {
    return { mode: 'repeat', frequencyPreset: 'every30m', time: '09:00', customCron: cron, fireAt: '' }
  }
  if (cron === '0 * * * *') {
    return { mode: 'repeat', frequencyPreset: 'hourly', time: '09:00', customCron: cron, fireAt: '' }
  }
  const parts = cron.split(/\s+/)
  if (parts.length === 5) {
    const [min, hour, dom, mon, dow] = parts
    if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*') {
      const time = `${String(Number(hour)).padStart(2, '0')}:${String(Number(min)).padStart(2, '0')}`
      if (dow === '*') return { mode: 'repeat', frequencyPreset: 'daily', time, customCron: cron, fireAt: '' }
      if (dow === '1-5') return { mode: 'repeat', frequencyPreset: 'weekdays', time, customCron: cron, fireAt: '' }
      if (dow === '5') return { mode: 'repeat', frequencyPreset: 'friday', time, customCron: cron, fireAt: '' }
    }
  }
  return { mode: 'repeat', frequencyPreset: 'custom', time: '09:00', customCron: cron || '0 9 * * 1-5', fireAt: '' }
}

function toDatetimeLocalValue(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.message && err.message !== `HTTP ${err.status}`) {
    return `${fallback} (${err.message})`
  }
  return fallback
}

function selectionKey(provider: string, modelId: string): string {
  return `${provider}::${modelId}`
}

function parseSelectionKey(value: string): { provider: string; modelId: string } {
  const idx = value.indexOf('::')
  if (idx === -1) return { provider: 'openrouter', modelId: value }
  return { provider: value.slice(0, idx), modelId: value.slice(idx + 2) }
}

export function SchedulesPage({ embedded = false }: { embedded?: boolean }) {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [items, setItems] = useState<ScheduledChat[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [availableModels, setAvailableModels] = useState<ProviderModel[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<ScheduleTab>('active')
  const [searchQuery, setSearchQuery] = useState('')

  // Form / Inspector state (serves both Create and Edit)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [provider, setProvider] = useState<string>('openrouter')
  const [modelId, setModelId] = useState('model-1')
  const [projectId, setProjectId] = useState('')
  const [mode, setMode] = useState<RecurrenceMode>('repeat')
  const [frequencyPreset, setFrequencyPreset] = useState<FrequencyPreset>('weekdays')
  const [customCron, setCustomCron] = useState('0 9 * * 1-5')
  const [fireAt, setFireAt] = useState('')
  const [time, setTime] = useState('09:00')
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE)

  // Interaction feedback states
  const [saving, setSaving] = useState(false)
  const [busyScheduleId, setBusyScheduleId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [confirmClearHistory, setConfirmClearHistory] = useState(false)
  const [clearingHistory, setClearingHistory] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [testFeedback, setTestFeedback] = useState<string | null>(null)

  const promptRef = useRef<HTMLTextAreaElement | null>(null)
  const hasUserPickedModel = useRef(false)

  const showNotice = useCallback((text: string) => {
    setNotice(text)
  }, [])

  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(null), 4000)
    return () => window.clearTimeout(timer)
  }, [notice])

  const reload = useCallback(
    () =>
      Promise.all([listScheduledChats(client), listProjects(client)]).then(([schedules, availableProjects]) => {
        setItems(schedules.items)
        setProjects(availableProjects.items)
        resetSettingsBadgeCache()
      }),
    [client],
  )

  useEffect(() => {
    let active = true
    void reload()
      .catch(() => {
        if (active) setError('Não foi possível carregar as ações agendadas.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [reload])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.allSettled(
      PROVIDER_NAMES.map((providerName) => listProviderModels(client, providerName, controller.signal)),
    ).then((results) => {
      if (controller.signal.aborted) return
      const collected = results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
      const sorted = [...collected].sort((a, b) => {
        if (a.provider !== b.provider) return a.provider.localeCompare(b.provider)
        if (a.is_favorite !== b.is_favorite) return a.is_favorite ? -1 : 1
        return a.display_name.localeCompare(b.display_name)
      })
      setAvailableModels(sorted)
      if (sorted.length > 0 && !hasUserPickedModel.current) {
        const preferred = sorted.find((item) => item.is_favorite) ?? sorted[0]
        if (preferred) {
          setProvider(preferred.provider)
          setModelId(preferred.model_id)
        }
      }
    })
    return () => controller.abort()
  }, [client])

  const defaultModel = useMemo(() => {
    const preferred = availableModels.find((item) => item.is_favorite) ?? availableModels[0]
    return {
      provider: preferred?.provider ?? 'openrouter',
      modelId: preferred?.model_id ?? 'model-1',
    }
  }, [availableModels])

  const modelsByProvider = useMemo(() => {
    const grouped = new Map<string, ProviderModel[]>()
    for (const model of availableModels) {
      const list = grouped.get(model.provider) ?? []
      list.push(model)
      grouped.set(model.provider, list)
    }
    return Array.from(grouped.entries())
  }, [availableModels])

  const modelLookup = useMemo(() => {
    const map = new Map<string, ProviderModel>()
    for (const model of availableModels) {
      map.set(selectionKey(model.provider, model.model_id), model)
    }
    return map
  }, [availableModels])

  const timezoneOptions = useMemo(() => {
    const exists = BASE_TIMEZONE_OPTIONS.some(([value]) => value === timezone)
    if (exists) return BASE_TIMEZONE_OPTIONS
    const label = timezone === 'Etc/GMT+3' ? 'Brasília (UTC−03:00)' : `${formatTimezoneShort(timezone)} (${timezone})`
    return [[timezone, label] as const, ...BASE_TIMEZONE_OPTIONS]
  }, [timezone])

  const editingItem = useMemo(
    () => (editingId ? items.find((item) => item.schedule_id === editingId) ?? null : null),
    [editingId, items],
  )

  const resetToCreateMode = useCallback(() => {
    setEditingId(null)
    setMessage('')
    setProvider(defaultModel.provider)
    setModelId(defaultModel.modelId)
    setProjectId('')
    setMode('repeat')
    setFrequencyPreset('weekdays')
    setCustomCron('0 9 * * 1-5')
    setFireAt('')
    setTime('09:00')
    setTimezone(DEFAULT_TIMEZONE)
    setTestFeedback(null)
    setError(null)
  }, [defaultModel])

  function startEditing(item: ScheduledChat) {
    const parsed = parseScheduleIntoForm(item)
    hasUserPickedModel.current = true
    setEditingId(item.schedule_id)
    setMessage(item.message)
    setProvider(item.provider || defaultModel.provider)
    setModelId(item.model_id || defaultModel.modelId)
    setProjectId(item.project_id ?? '')
    setMode(parsed.mode)
    setFrequencyPreset(parsed.frequencyPreset)
    setTime(parsed.time)
    setCustomCron(parsed.customCron)
    setFireAt(parsed.fireAt)
    setTimezone(item.timezone || DEFAULT_TIMEZONE)
    setTestFeedback(null)
    setError(null)
    setConfirmDeleteId(null)
    promptRef.current?.focus()
  }

  function handleFrequencyChange(value: FrequencyPreset) {
    setFrequencyPreset(value)
    if (value === 'friday' && time === '09:00') {
      setTime('18:00')
    }
  }

  function handleTestNow() {
    const promptPreview = message.trim() || 'Execução de teste da rotina'
    const cronExpr = buildCronExpression(frequencyPreset, time, customCron)
    const scheduleDesc = mode === 'once' && fireAt ? `Uma vez (${fireAt})` : formatHumanCron(cronExpr)
    setTestFeedback(`Simulação pronta: "${promptPreview}" · ${scheduleDesc} (${formatTimezoneShort(timezone)})`)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setTestFeedback(null)
    const trimmed = message.trim()
    if (!trimmed) {
      setError('Descreva a instrução que o Orin deve executar.')
      return
    }
    if (!modelId || !provider) {
      setError('Selecione um modelo válido antes de salvar.')
      return
    }

    const effectiveMode: RecurrenceMode = mode === 'once' || (mode !== 'repeat' && fireAt.trim() !== '') ? 'once' : 'repeat'
    if (effectiveMode === 'once' && !fireAt.trim()) {
      setError('Informe a data e a hora para uma execução única.')
      return
    }

    const cronExpression = buildCronExpression(frequencyPreset, time, customCron)
    const recurrence: ScheduleRecurrence =
      effectiveMode === 'once'
        ? { kind: 'once', fire_at: fireAt }
        : { kind: 'cron', expression: cronExpression }

    setSaving(true)
    try {
      if (editingId) {
        await updateScheduledChat(client, editingId, {
          message: trimmed,
          provider: provider as ProviderName,
          model_id: modelId,
          timezone,
          recurrence,
          project_id: projectId || null,
        })
        await reload()
        resetToCreateMode()
        showNotice('Agendamento atualizado.')
      } else {
        await createScheduledChat(client, {
          message: trimmed,
          provider: provider as ProviderName,
          model_id: modelId,
          timezone,
          recurrence,
          project_id: projectId || null,
        })
        setMessage('')
        setFireAt('')
        await reload()
        showNotice('Nova ação agendada.')
      }
    } catch (err) {
      setError(
        formatErrorMessage(
          err,
          editingId
            ? 'Não foi possível salvar as alterações da agenda.'
            : 'Não foi possível criar a agenda. Confira horário, fuso e modelo.',
        ),
      )
    } finally {
      setSaving(false)
    }
  }

  async function togglePause(item: ScheduledChat) {
    setError(null)
    setBusyScheduleId(item.schedule_id)
    const nextState: 'ACTIVE' | 'PAUSED' = item.state === 'ACTIVE' ? 'PAUSED' : 'ACTIVE'
    try {
      await updateScheduledChat(client, item.schedule_id, { state: nextState })
      await reload()
      showNotice(nextState === 'PAUSED' ? 'Ação pausada.' : 'Ação retomada.')
    } catch (err) {
      setError(
        formatErrorMessage(
          err,
          nextState === 'PAUSED' ? 'Não foi possível pausar a ação.' : 'Não foi possível retomar a ação.',
        ),
      )
    } finally {
      setBusyScheduleId(null)
    }
  }

  async function removeSchedule(scheduleId: string) {
    setError(null)
    setBusyScheduleId(scheduleId)
    try {
      await deleteScheduledChat(client, scheduleId)
      if (editingId === scheduleId) {
        resetToCreateMode()
      }
      setConfirmDeleteId(null)
      await reload()
      showNotice('Agendamento excluído.')
    } catch (err) {
      setError(formatErrorMessage(err, 'Não foi possível excluir o agendamento.'))
    } finally {
      setBusyScheduleId(null)
    }
  }

  async function clearCompletedHistory() {
    if (completedApiItems.length === 0) return
    setError(null)
    setClearingHistory(true)
    try {
      const results = await Promise.allSettled(
        completedApiItems.map((item) => deleteScheduledChat(client, item.schedule_id)),
      )
      const failedCount = results.filter((r) => r.status === 'rejected').length
      if (editingId && completedApiItems.some((item) => item.schedule_id === editingId)) {
        resetToCreateMode()
      }
      setConfirmClearHistory(false)
      await reload()
      if (failedCount > 0) {
        setError(`Não foi possível remover ${failedCount} item(ns) do histórico.`)
      } else {
        showNotice('Histórico limpo.')
      }
    } finally {
      setClearingHistory(false)
    }
  }

  const activeApiItems = items.filter((item) => item.state === 'ACTIVE')
  const pausedApiItems = items.filter((item) => item.state === 'PAUSED')
  const completedApiItems = items.filter((item) => item.state !== 'ACTIVE' && item.state !== 'PAUSED')

  const tabItems =
    activeTab === 'active'
      ? activeApiItems
      : activeTab === 'paused'
        ? pausedApiItems
        : completedApiItems

  const query = searchQuery.trim().toLowerCase()
  const visibleApiItems = query
    ? tabItems.filter((item) => {
        const modelLabel = formatModelDisplayName(item).toLowerCase()
        const projectName = resolveProjectName(item, projects).toLowerCase()
        return (
          item.message.toLowerCase().includes(query) ||
          modelLabel.includes(query) ||
          item.model_id.toLowerCase().includes(query) ||
          projectName.includes(query) ||
          (item.conversation_title ?? '').toLowerCase().includes(query)
        )
      })
    : tabItems

  const selectedModelKey = selectionKey(provider, modelId)
  const hasSelectedInCatalog = modelLookup.has(selectedModelKey)
  const fallbackModelLabel = editingItem
    ? `${formatModelDisplayName(editingItem)} (${formatProviderShort(editingItem.provider)})`
    : `${modelId} (${formatProviderShort(provider)})`

  const showTimeInput = mode === 'repeat' && frequencyPreset !== 'every30m' && frequencyPreset !== 'hourly' && frequencyPreset !== 'custom'

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
          onClick={() => {
            resetToCreateMode()
            promptRef.current?.focus()
          }}
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
            onClick={() => {
              setActiveTab('active')
              setConfirmDeleteId(null)
              setConfirmClearHistory(false)
            }}
          >
            Ativas <span>{activeApiItems.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'paused'}
            className={`schedules-ref09__tab${activeTab === 'paused' ? ' is-active' : ''}`}
            data-testid="schedules-tab-paused"
            onClick={() => {
              setActiveTab('paused')
              setConfirmDeleteId(null)
              setConfirmClearHistory(false)
            }}
          >
            Pausadas <span>{pausedApiItems.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'completed'}
            className={`schedules-ref09__tab${activeTab === 'completed' ? ' is-active' : ''}`}
            data-testid="schedules-tab-completed"
            onClick={() => {
              setActiveTab('completed')
              setConfirmDeleteId(null)
            }}
          >
            Concluídas <span>{completedApiItems.length}</span>
          </button>
        </div>

        <div className="schedules-ref09__toolbar-controls">
          {tabItems.length > 3 && (
            <input
              type="search"
              className="schedules-ref09__search"
              placeholder="Filtrar por texto, modelo ou projeto..."
              aria-label="Filtrar ações agendadas"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          )}
          {activeTab === 'completed' && completedApiItems.length > 0 && (
            confirmClearHistory ? (
              <div className="schedules-inline-confirm">
                <span>Limpar {completedApiItems.length}?</span>
                <button
                  type="button"
                  className="schedules-action-btn is-danger"
                  disabled={clearingHistory}
                  onClick={() => void clearCompletedHistory()}
                >
                  {clearingHistory ? 'Limpando...' : 'Confirmar'}
                </button>
                <button
                  type="button"
                  className="schedules-action-btn"
                  disabled={clearingHistory}
                  onClick={() => setConfirmClearHistory(false)}
                >
                  Cancelar
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="schedules-action-btn schedules-ref09__clear-btn"
                data-testid="schedules-clear-history"
                onClick={() => setConfirmClearHistory(true)}
              >
                Limpar histórico
              </button>
            )
          )}
        </div>
      </div>

      {error && (
        <p className="schedules-page__banner schedules-page__banner--error" role="alert">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="schedules-page__banner schedules-page__banner--notice" role="status">
          {notice}
        </p>
      )}

      <div className="schedules-ref09__master-detail">
        <div className="schedules-ref09__table-card">
          <table className="schedules-table" data-testid="schedules-table">
            <thead>
              <tr>
                <th scope="col" className="schedules-table__col-action">AÇÃO</th>
                <th scope="col" className="schedules-table__col-when">QUANDO</th>
                <th scope="col" className="schedules-table__col-project">PROJETO</th>
                <th scope="col" className="schedules-table__col-status">STATUS</th>
              </tr>
            </thead>
            <tbody data-testid="schedules-table-body">
              {loading && items.length === 0 ? (
                <tr>
                  <td colSpan={4} className="schedules-table__empty">
                    Carregando ações agendadas...
                  </td>
                </tr>
              ) : visibleApiItems.length > 0 ? (
                visibleApiItems.map((item) => (
                  <ApiScheduleTableRow
                    key={item.schedule_id}
                    item={item}
                    projects={projects}
                    modelLookup={modelLookup}
                    isSelected={editingId === item.schedule_id}
                    isBusy={busyScheduleId === item.schedule_id}
                    isConfirmingDelete={confirmDeleteId === item.schedule_id}
                    onEdit={() => startEditing(item)}
                    onTogglePause={() => void togglePause(item)}
                    onRequestDelete={() => setConfirmDeleteId(item.schedule_id)}
                    onCancelDelete={() => setConfirmDeleteId(null)}
                    onConfirmDelete={() => void removeSchedule(item.schedule_id)}
                  />
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="schedules-table__empty" data-testid="schedules-empty-state">
                    {searchQuery.trim()
                      ? 'Nenhuma ação corresponde à busca atual.'
                      : activeTab === 'active'
                        ? 'Nenhuma ação agendada neste filtro. Use o painel ao lado para criar uma rotina.'
                        : activeTab === 'paused'
                          ? 'Nenhuma ação pausada no momento.'
                          : 'Nenhuma ação concluída ou cancelada no histórico.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <aside
          className={`schedules-drawer${editingId ? ' is-editing' : ''}`}
          data-testid="schedules-drawer"
          aria-label={editingId ? 'Editar ação' : 'Nova ação'}
        >
          <header className="schedules-drawer__header">
            <div className="schedules-drawer__header-title">
              <h3>{editingId ? 'Editar ação' : 'Nova ação'}</h3>
              {editingId && (
                <button
                  type="button"
                  className="schedules-drawer__close-edit"
                  onClick={resetToCreateMode}
                >
                  Voltar para nova
                </button>
              )}
            </div>
            {editingItem && (
              <p className="schedules-drawer__editing-meta">
                Status atual: <strong>{stateLabel(editingItem.state)}</strong>
                {editingItem.conversation_title ? ` · Chat "${editingItem.conversation_title}"` : ''}
              </p>
            )}
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
                ref={promptRef}
                id="schedule-prompt"
                aria-label="Instrução"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                placeholder="Ex.: revise as tarefas abertas do projeto e me traga um resumo"
                rows={editingId ? 5 : 3}
              />
            </label>

            {mode === 'repeat' ? (
              <>
                <div className={showTimeInput ? 'schedules-drawer__row-2col' : undefined}>
                  <label className="schedules-drawer__field" htmlFor="schedule-frequency">
                    <span>Frequência</span>
                    <select
                      id="schedule-frequency"
                      aria-label="Frequência"
                      value={frequencyPreset}
                      onChange={(event) => handleFrequencyChange(event.target.value as FrequencyPreset)}
                    >
                      <option value="daily">Todos os dias</option>
                      <option value="weekdays">Segunda a sexta</option>
                      <option value="friday">Toda sexta</option>
                      <option value="every30m">A cada 30 minutos</option>
                      <option value="hourly">A cada hora</option>
                      <option value="custom">Personalizado (cron)</option>
                    </select>
                  </label>

                  {showTimeInput && (
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
                  )}
                </div>

                {frequencyPreset === 'custom' && (
                  <label className="schedules-drawer__field" htmlFor="schedule-custom-cron">
                    <span>Expressão cron (5 campos)</span>
                    <input
                      id="schedule-custom-cron"
                      type="text"
                      aria-label="Expressão cron"
                      placeholder="0 9 * * 1-5"
                      value={customCron}
                      onChange={(event) => setCustomCron(event.target.value)}
                    />
                  </label>
                )}
              </>
            ) : (
              <label className="schedules-drawer__field" htmlFor="schedule-fire-at">
                <span>Data e hora</span>
                <input
                  id="schedule-fire-at"
                  type="datetime-local"
                  aria-label="Data e hora"
                  value={fireAt}
                  onChange={(event) => setFireAt(event.target.value)}
                />
              </label>
            )}

            <label className="schedules-drawer__field" htmlFor="schedule-model">
              <span>Modelo</span>
              <select
                id="schedule-model"
                aria-label="Modelo"
                value={selectedModelKey}
                onChange={(event) => {
                  hasUserPickedModel.current = true
                  const next = parseSelectionKey(event.target.value)
                  setProvider(next.provider)
                  setModelId(next.modelId)
                }}
              >
                {!hasSelectedInCatalog && (
                  <option value={selectedModelKey}>{fallbackModelLabel}</option>
                )}
                {modelsByProvider.map(([providerName, models]) => (
                  <optgroup key={providerName} label={formatProviderShort(providerName)}>
                    {models.map((model) => {
                      const cleanName = model.display_name.replace(/\s*\(Assinatura [^)]+\)\s*$/i, '').trim()
                      return (
                        <option
                          key={selectionKey(model.provider, model.model_id)}
                          value={selectionKey(model.provider, model.model_id)}
                        >
                          {model.is_favorite ? `★ ${cleanName}` : cleanName}
                        </option>
                      )
                    })}
                  </optgroup>
                ))}
              </select>
            </label>

            <div className={projects.length > 0 ? 'schedules-drawer__row-2col' : undefined}>
              <label className="schedules-drawer__field" htmlFor="schedule-timezone">
                <span>Fuso horário</span>
                <select
                  id="schedule-timezone"
                  aria-label="Fuso horário"
                  value={timezone}
                  onChange={(event) => setTimezone(event.target.value)}
                >
                  {timezoneOptions.map(([value, labelText]) => (
                    <option key={value} value={value}>
                      {labelText}
                    </option>
                  ))}
                </select>
              </label>

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
            </div>

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
              {editingId ? (
                <button
                  type="button"
                  className="schedules-drawer__test-btn"
                  disabled={saving}
                  onClick={resetToCreateMode}
                >
                  Cancelar
                </button>
              ) : (
                <button
                  type="button"
                  className="schedules-drawer__test-btn"
                  data-testid="schedule-test-now"
                  onClick={handleTestNow}
                >
                  Testar agora
                </button>
              )}
              <button
                type="submit"
                className="schedules-drawer__submit"
                data-testid="schedules-submit-button"
                aria-label={editingId ? 'Salvar alterações' : 'Criar tarefa'}
                disabled={saving}
              >
                {saving ? 'Salvando...' : editingId ? 'Salvar alterações' : 'Criar ação'}
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
  modelLookup,
  isSelected,
  isBusy,
  isConfirmingDelete,
  onEdit,
  onTogglePause,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  item: ScheduledChat
  projects: Project[]
  modelLookup: Map<string, ProviderModel>
  isSelected: boolean
  isBusy: boolean
  isConfirmingDelete: boolean
  onEdit: () => void
  onTogglePause: () => void
  onRequestDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}) {
  const effectiveProjectId = item.project_id ?? item.conversation_project_id ?? null
  const conversationPath = item.conversation_id
    ? effectiveProjectId
      ? `/projects/${effectiveProjectId}/chats/${item.conversation_id}`
      : `/chats/${item.conversation_id}`
    : null

  const projectName = resolveProjectName(item, projects)
  const summary = summarizeScheduleMessage(item.message, 92)

  const catalogModel = modelLookup.get(selectionKey(item.provider, item.model_id))
  const modelLabel = formatModelDisplayName({
    provider: item.provider,
    model_id: item.model_id,
    model_display_name: item.model_display_name ?? catalogModel?.display_name ?? null,
  })

  const canPauseOrResume = item.state === 'ACTIVE' || item.state === 'PAUSED'

  return (
    <tr
      className={`schedules-table__row${isSelected ? ' is-selected' : ''}`}
      data-testid={`schedule-row-${item.schedule_id}`}
    >
      <td className="schedules-table__col-action">
        <button
          type="button"
          className="schedules-table__title-trigger"
          title={summary.isTruncated ? item.message : undefined}
          onClick={onEdit}
        >
          <span className="schedules-table__action-title">{summary.title}</span>
        </button>
        <div className="schedules-table__action-sub">
          <span
            className="schedules-model-chip"
            title={`${item.provider} / ${item.model_id}`}
            data-testid={`schedule-model-${item.schedule_id}`}
          >
            {modelLabel}
          </span>
          {item.byAgent && <span className="schedules-badge schedules-badge--agent">pelo agente</span>}
          {conversationPath && (
            <Link className="schedules-table__chat-link" to={conversationPath}>
              {item.conversation_title ? `↗ ${item.conversation_title}` : 'Abrir chat'}
            </Link>
          )}
        </div>
      </td>

      <td className="schedules-table__col-when">
        <span className="schedules-table__when-main">{formatHumanWhen(item)}</span>
        <span className="schedules-table__cron-meta" title={item.cron ? `cron ${item.cron} · ${item.timezone ?? DEFAULT_TIMEZONE}` : undefined}>
          {item.next_fire_at
            ? `${formatNextFire(item.next_fire_at)} · ${formatTimezoneShort(item.timezone)}`
            : item.cron
              ? `cron ${item.cron} · ${formatTimezoneShort(item.timezone)}`
              : formatNextFire(item.next_fire_at)}
        </span>
      </td>

      <td className="schedules-table__col-project">
        <span className="schedules-table__project-pill" title={projectName}>{projectName}</span>
      </td>

      <td className="schedules-table__col-status">
        <div className="schedules-status-cell">
          <span className={`schedules-status-pill ${statusPillClass(item.state)}`}>{stateLabel(item.state)}</span>
          <div className="schedules-row-actions">
            {isConfirmingDelete ? (
              <div className="schedules-inline-confirm" data-testid={`schedule-delete-confirm-${item.schedule_id}`}>
                <button
                  type="button"
                  className="schedules-action-btn is-danger"
                  disabled={isBusy}
                  onClick={onConfirmDelete}
                >
                  {isBusy ? '...' : 'Confirmar'}
                </button>
                <button
                  type="button"
                  className="schedules-action-btn"
                  disabled={isBusy}
                  onClick={onCancelDelete}
                >
                  Não
                </button>
              </div>
            ) : (
              <>
                <button
                  type="button"
                  className="schedules-icon-btn"
                  data-testid={`schedule-edit-${item.schedule_id}`}
                  aria-label="Editar"
                  title="Editar agendamento"
                  disabled={isBusy}
                  onClick={onEdit}
                >
                  <PencilIcon />
                </button>
                {canPauseOrResume && (
                  <button
                    type="button"
                    className="schedules-icon-btn"
                    data-testid={`schedule-toggle-${item.schedule_id}`}
                    aria-label={item.state === 'ACTIVE' ? 'Pausar' : 'Retomar'}
                    title={item.state === 'ACTIVE' ? 'Pausar agendamento' : 'Retomar agendamento'}
                    disabled={isBusy}
                    onClick={onTogglePause}
                  >
                    {item.state === 'ACTIVE' ? <PauseIcon /> : <PlayIcon />}
                  </button>
                )}
                <button
                  type="button"
                  className="schedules-icon-btn is-danger-ghost"
                  data-testid={`schedule-delete-${item.schedule_id}`}
                  aria-label="Excluir"
                  title="Excluir agendamento"
                  disabled={isBusy}
                  onClick={onRequestDelete}
                >
                  <TrashIcon />
                </button>
              </>
            )}
          </div>
        </div>
      </td>
    </tr>
  )
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11.3 2.7a1.5 1.5 0 0 1 2.1 2.1L5.6 12.6 2.5 13.5l.9-3.1 7.9-7.7Z" />
    </svg>
  )
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M5.5 3.5v9M10.5 3.5v9" />
    </svg>
  )
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M5 3.4a.6.6 0 0 1 .9-.5l7.2 4.6a.6.6 0 0 1 0 1l-7.2 4.6a.6.6 0 0 1-.9-.5V3.4Z" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.8 4.2h10.4M6.2 4.2V2.9c0-.4.3-.7.7-.7h2.2c.4 0 .7.3.7.7v1.3M4.3 4.2l.6 8.5c0 .6.5 1.1 1.1 1.1h4c.6 0 1.1-.5 1.1-1.1l.6-8.5" />
    </svg>
  )
}

function resolveProjectName(item: ScheduledChat, projects: Project[]): string {
  const effectiveProjectId = item.project_id ?? item.conversation_project_id ?? null
  if (!effectiveProjectId) return 'Geral'
  return projects.find((p) => p.project_id === effectiveProjectId)?.name ?? effectiveProjectId
}

function formatNextFire(value: string | null): string {
  if (!value) return 'Sem próxima execução'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Data indisponível'
  return `Próx. ${date.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`
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
