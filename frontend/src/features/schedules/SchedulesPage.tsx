import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { createBrowserApiClient } from '../../api/client'
import { listProjects, type Project } from '../../api/projects'
import { PROVIDER_NAMES, listProviderModels, type ProviderModel, type ProviderName } from '../../api/providers'
import { cancelScheduledChat, createScheduledChat, listScheduledChats, type ScheduleRecurrence, type ScheduledChat } from '../../api/schedules'
import { Brand } from '../../components/Brand'
import { ModelPicker } from '../../components/ModelPicker'

export function SchedulesPage() {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [items, setItems] = useState<ScheduledChat[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [message, setMessage] = useState('')
  const [provider, setProvider] = useState<ProviderName>('openrouter')
  const [models, setModels] = useState<ProviderModel[]>([])
  const [modelId, setModelId] = useState('')
  const [projectId, setProjectId] = useState('')
  const [kind, setKind] = useState<ScheduleRecurrence['kind']>('once')
  const [fireAt, setFireAt] = useState('')
  const [time, setTime] = useState('09:00')
  const [weekday, setWeekday] = useState('0')
  const [error, setError] = useState<string | null>(null)
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', [])

  const reload = useCallback(
    () => Promise.all([listScheduledChats(client), listProjects(client)]).then(([schedules, availableProjects]) => {
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
    return () => { active = false }
  }, [reload])

  useEffect(() => {
    const controller = new AbortController()
    void listProviderModels(client, provider, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setModels(value)
          setModelId(value.find((item) => item.is_favorite)?.model_id ?? value[0]?.model_id ?? '')
        }
      })
    .catch(() => {
      if (!controller.signal.aborted) setError('Não foi possível carregar os modelos.')
    })
    return () => controller.abort()
  }, [client, provider])

  function changeProvider(value: ProviderName) {
    setProvider(value)
    setModels([])
    setModelId('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (!message.trim() || !modelId || (kind === 'once' && !fireAt)) return
    const recurrence: ScheduleRecurrence = kind === 'once'
      ? { kind, fire_at: new Date(fireAt).toISOString() }
      : kind === 'hourly' ? { kind } : kind === 'daily' ? { kind, time_of_day: time } : { kind, time_of_day: time, weekday: Number(weekday) }
    try {
      await createScheduledChat(client, { message: message.trim(), provider, model_id: modelId, timezone, recurrence, project_id: projectId || null })
      setMessage('')
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

  const activeCount = items.filter((item) => item.state === 'ACTIVE').length

  return (
    <main className="app-shell schedules-shell">
      <header className="topbar">
        <Brand to="/" />
        <span className="topbar__context">Automações</span>
        <Link className="topbar__back" to="/">Voltar ao chat</Link>
      </header>

      <section className="schedules-page" aria-labelledby="schedules-title">
        <header className="schedules-page__heading">
          <div>
            <p className="eyebrow">AUTOMAÇÃO / AGENDA</p>
            <h1 id="schedules-title">Tarefas agendadas</h1>
            <p className="schedules-page__lede">Deixe o Orin cuidar de um trabalho no momento certo. Cada disparo continua sendo uma conversa normal, com o mesmo projeto e workspace.</p>
          </div>
          <div className="schedules-page__count" aria-label={`${activeCount} tarefas ativas`}>
            <strong>{activeCount.toString().padStart(2, '0')}</strong>
            <span>ativas agora</span>
          </div>
        </header>

        {error && <p className="schedules-page__error" role="alert">{error}</p>}

        <div className="schedules-page__grid">
          <form className="schedule-form" onSubmit={(event) => void submit(event)}>
            <header className="schedule-form__header">
              <span className="schedule-form__index" aria-hidden="true">01</span>
              <div>
                <p className="eyebrow">NOVA TAREFA</p>
                <h2>Agendar uma execução</h2>
                <p>Defina o que precisa acontecer e quando o agente deve começar.</p>
              </div>
            </header>

            <div className="schedule-form__body">
              <label className="schedule-form__field schedule-form__field--wide" htmlFor="schedule-message">
                <span>Instrução</span>
                <textarea id="schedule-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Ex.: revise as tarefas abertas do projeto e me traga um resumo" />
              </label>

              <div className="schedule-form__field schedule-form__field--wide">
                <span className="schedule-form__label">Provider e modelo</span>
                <ModelPicker providers={[...PROVIDER_NAMES]} provider={provider} onProviderChange={changeProvider} models={models} modelId={modelId} onModelChange={setModelId} />
              </div>

              <div className="schedule-form__columns">
                <label className="schedule-form__field" htmlFor="schedule-project">
                  <span>Projeto <em>opcional</em></span>
                  <select id="schedule-project" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                    <option value="">Chat independente</option>
                    {projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name}</option>)}
                  </select>
                </label>
                <label className="schedule-form__field" htmlFor="schedule-recurrence">
                  <span>Frequência</span>
                  <select id="schedule-recurrence" value={kind} onChange={(event) => setKind(event.target.value as ScheduleRecurrence['kind'])}>
                    <option value="once">Uma vez</option>
                    <option value="hourly">A cada hora</option>
                    <option value="daily">Todos os dias</option>
                    <option value="weekly">Toda semana</option>
                  </select>
                </label>
              </div>

              <div className="schedule-form__timing">
                <span className="schedule-form__timing-mark" aria-hidden="true">↗</span>
                <div className="schedule-form__timing-fields">
                  <p className="schedule-form__label">Próximo disparo</p>
                  {kind === 'once' && <label className="schedule-form__field" htmlFor="schedule-fire-at"><span>Data e hora</span><input id="schedule-fire-at" type="datetime-local" value={fireAt} onChange={(event) => setFireAt(event.target.value)} required /></label>}
                  {(kind === 'daily' || kind === 'weekly') && <label className="schedule-form__field" htmlFor="schedule-time"><span>Horário</span><input id="schedule-time" type="time" value={time} onChange={(event) => setTime(event.target.value)} required /></label>}
                  {kind === 'hourly' && <p className="schedule-form__timing-copy">A tarefa será executada no início de cada hora.</p>}
                  {kind === 'weekly' && <label className="schedule-form__field" htmlFor="schedule-weekday"><span>Dia da semana</span><select id="schedule-weekday" value={weekday} onChange={(event) => setWeekday(event.target.value)}>{['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'].map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label>}
                </div>
              </div>
            </div>

            <footer className="schedule-form__footer">
              <span>Fuso local: <strong>{timezone}</strong></span>
              <button className="button button--primary" type="submit">Criar tarefa</button>
            </footer>
          </form>

          <aside className="schedules-page__aside">
            <section className="schedule-context-card">
              <div className="schedule-context-card__icon" aria-hidden="true">↗</div>
              <p className="eyebrow">COMO FUNCIONA</p>
              <h2>Automação que continua no chat</h2>
              <p>Recorrências reutilizam a mesma conversa. O agente recebe o contexto do projeto e trabalha no workspace já conhecido.</p>
              <dl>
                <div><dt>Execução</dt><dd>Turno de chat normal</dd></div>
                <div><dt>Fuso horário</dt><dd>{timezone}</dd></div>
              </dl>
            </section>

            <section className="schedule-list-panel" aria-labelledby="schedule-list-title">
              <header className="schedule-list-panel__header">
                <div><p className="eyebrow">SEU FLUXO</p><h2 id="schedule-list-title">Tarefas ativas</h2></div>
                <span>{items.length.toString().padStart(2, '0')}</span>
              </header>
              {items.length === 0
                ? <div className="schedule-list-panel__empty"><span aria-hidden="true">○</span><p>Nenhuma tarefa agendada ainda.</p><small>As tarefas que você criar aparecerão aqui.</small></div>
                : <ul className="schedule-list">{items.map((item) => <ScheduleRow key={item.schedule_id} item={item} onCancel={cancel} />)}</ul>}
            </section>
          </aside>
        </div>
      </section>
    </main>
  )
}

function ScheduleRow({ item, onCancel }: { item: ScheduledChat; onCancel: (scheduleId: string) => Promise<void> }) {
  const conversationPath = item.conversation_id
    ? item.project_id ? `/projects/${item.project_id}/chats/${item.conversation_id}` : `/chats/${item.conversation_id}`
    : null
  return <li className="schedule-row">
    <div className="schedule-row__marker" aria-hidden="true" />
    <div className="schedule-row__content">
      <strong>{item.message}</strong>
      <span>{label(item.recurrence)} · {formatNextFire(item.next_fire_at)}</span>
      <small>{item.provider} · {item.model_id}</small>
    </div>
    <div className="schedule-row__actions">
      <span className={`schedule-row__state schedule-row__state--${item.state.toLowerCase()}`}>{stateLabel(item.state)}</span>
      {conversationPath && <Link to={conversationPath}>Abrir chat</Link>}
      {item.state === 'ACTIVE' && <button type="button" onClick={() => void onCancel(item.schedule_id)}>Cancelar</button>}
    </div>
  </li>
}

function formatNextFire(value: string | null): string {
  if (!value) return 'sem próxima execução'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'data indisponível' : `próxima · ${date.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}`
}

function stateLabel(value: string): string {
  return ({ ACTIVE: 'Ativa', PAUSED: 'Pausada', COMPLETED: 'Concluída', CANCELLED: 'Cancelada' } as Record<string, string>)[value] ?? value
}

function label(kind: ScheduledChat['recurrence']): string {
  return ({ once: 'uma vez', hourly: 'a cada hora', daily: 'diária', weekly: 'semanal' })[kind]
}
