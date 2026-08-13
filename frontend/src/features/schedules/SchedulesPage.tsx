import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { createBrowserApiClient } from '../../api/client'
import { listProjects, type Project } from '../../api/projects'
import { PROVIDER_NAMES, listProviderModels, type ProviderModel, type ProviderName } from '../../api/providers'
import { cancelScheduledChat, createScheduledChat, listScheduledChats, type ScheduleRecurrence, type ScheduledChat } from '../../api/schedules'
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
      .catch(() => setError('Não foi possível carregar os modelos.'))
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
      await createScheduledChat(client, { message: message.trim(), provider, model_id: modelId, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', recurrence, project_id: projectId || null })
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

  return <main className="skills-shell"><section className="skills-library"><header className="skills-library__heading"><div><p className="eyebrow">AUTOMAÇÃO</p><h1>Tarefas agendadas</h1><p>O disparo cria um turno de chat normal. Recorrências preservam a conversa e o workspace.</p></div><Link className="ghost-button" to="/">Voltar ao chat</Link></header><form className="skills-form" onSubmit={(event) => void submit(event)}><div className="skills-form__heading"><div><p className="eyebrow">NOVA TAREFA</p><h2>Agendar execução</h2></div></div><label>Instrução<textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="O que o agente deve executar?" /></label><ModelPicker providers={[...PROVIDER_NAMES]} provider={provider} onProviderChange={changeProvider} models={models} modelId={modelId} onModelChange={setModelId} /><label>Projeto (opcional)<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Chat independente</option>{projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name}</option>)}</select></label><label>Recorrência<select value={kind} onChange={(event) => setKind(event.target.value as ScheduleRecurrence['kind'])}><option value="once">Uma vez</option><option value="hourly">A cada hora</option><option value="daily">Todos os dias</option><option value="weekly">Toda semana</option></select></label>{kind === 'once' && <label>Data e hora<input type="datetime-local" value={fireAt} onChange={(event) => setFireAt(event.target.value)} required /></label>}{(kind === 'daily' || kind === 'weekly') && <label>Horário<input type="time" value={time} onChange={(event) => setTime(event.target.value)} required /></label>}{kind === 'weekly' && <label>Dia da semana<select value={weekday} onChange={(event) => setWeekday(event.target.value)}>{['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'].map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label>}{error && <p className="skills-form__error" role="alert">{error}</p>}<div className="skills-form__actions"><button className="button button--primary" type="submit">Agendar</button></div></form><ul className="skills-list">{items.map((item) => <li key={item.schedule_id}><div className="skill-row"><div className="skill-row__main"><strong>{item.message}</strong><span>{item.provider} · {item.model_id} · {label(item.recurrence)}</span></div><div className="skill-row__meta"><span>{item.next_fire_at ? new Date(item.next_fire_at).toLocaleString() : item.state}</span>{item.conversation_id && <Link to={item.project_id ? `/projects/${item.project_id}/chats/${item.conversation_id}` : `/chats/${item.conversation_id}`}>abrir</Link>}{item.state === 'ACTIVE' && <button className="ghost-button" type="button" onClick={() => void cancel(item.schedule_id)}>Cancelar</button>}</div></div></li>)}</ul></section></main>
}

function label(kind: ScheduledChat['recurrence']): string {
  return ({ once: 'uma vez', hourly: 'a cada hora', daily: 'diária', weekly: 'semanal' })[kind]
}
