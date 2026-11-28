import type { ApiClient } from './client'
import { invalidResponseError } from './errors'
import type { ProviderName } from './providers'

export type ScheduleRecurrence =
  | { kind: 'once'; fire_at: string }
  | { kind: 'hourly' }
  | { kind: 'daily'; time_of_day: string }
  | { kind: 'weekly'; time_of_day: string; weekday: number }
  | { kind: 'cron'; expression: string }

/** `cron` and `byAgent` mark a job the agent scheduled with CronCreate, ScheduleWakeup or Monitor. */
export type ScheduledChat = {
  schedule_id: string
  state: string
  next_fire_at: string | null
  recurrence: ScheduleRecurrence['kind'] | 'cron'
  project_id: string | null
  conversation_project_id?: string | null
  conversation_title?: string | null
  message: string
  provider: string
  model_id: string
  model_display_name?: string | null
  conversation_id: string | null
  cron: string | null
  time_of_day?: string | null
  weekday?: number | null
  fire_at?: string | null
  byAgent: boolean
  timezone?: string
  ends_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  paused?: boolean
  projectLabel?: string
}

export type ScheduleItem = ScheduledChat

export type UpdateScheduledChatInput = {
  message?: string
  provider?: ProviderName | string
  model_id?: string
  timezone?: string
  recurrence?: ScheduleRecurrence
  project_id?: string | null
  state?: 'ACTIVE' | 'PAUSED'
}

const WEEKDAY_CRON_LABELS: Record<string, string> = {
  '0': 'Domingo',
  '7': 'Domingo',
  '1': 'Segunda',
  '2': 'Terça',
  '3': 'Quarta',
  '4': 'Quinta',
  '5': 'Sexta',
  '6': 'Sábado',
  '1-5': 'Seg a sex',
  '0,6': 'Finais de semana',
  '6,0': 'Finais de semana',
}

const WEEKDAY_INDEX_LABELS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'] as const

const PROVIDER_SHORT_LABELS: Record<string, string> = {
  'google-antigravity': 'Antigravity',
  'openai-codex': 'Codex',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA',
  opencode: 'OpenCode',
  omniroute: 'OmniRoute',
  ollama: 'Ollama',
}

export function formatProviderShort(provider: string): string {
  return PROVIDER_SHORT_LABELS[provider] ?? provider
}

export function formatModelDisplayName(
  item: Pick<ScheduledChat, 'provider' | 'model_id' | 'model_display_name'>,
): string {
  const raw = item.model_display_name?.trim()
  if (raw) {
    return raw.replace(/\s*\(Assinatura [^)]+\)\s*$/i, '').trim()
  }
  return item.model_id || 'Modelo não definido'
}

export function formatTimezoneShort(timezone?: string | null): string {
  if (!timezone) return 'BRT'
  if (timezone === 'America/Sao_Paulo' || timezone === 'Etc/GMT+3') return 'BRT'
  if (timezone === 'UTC' || timezone === 'Etc/UTC') return 'UTC'
  const city = timezone.split('/').pop()
  return city ? city.replace(/_/g, ' ') : timezone
}

export function summarizeScheduleMessage(message: string, maxLength = 96): {
  title: string
  isTruncated: boolean
} {
  const normalized = message.replace(/\s+/g, ' ').trim()
  if (!normalized) return { title: 'Ação sem descrição', isTruncated: false }
  if (normalized.length <= maxLength) return { title: normalized, isTruncated: false }
  const slice = normalized.slice(0, maxLength)
  const lastSpace = slice.lastIndexOf(' ')
  const cut = lastSpace > maxLength * 0.6 ? slice.slice(0, lastSpace) : slice
  return { title: `${cut.replace(/[.,;:\-\s]+$/, '')}…`, isTruncated: true }
}

export function formatHumanCron(
  input:
    | string
    | Pick<ScheduledChat, 'recurrence' | 'cron' | 'next_fire_at' | 'time_of_day' | 'weekday'>
    | null
    | undefined,
): string {
  if (!input) return 'Uma vez'
  const cron = (typeof input === 'string' ? input : input.cron)?.trim() ?? ''
  if (cron) {
    if (cron === '*/30 * * * *') return 'A cada 30 min'
    const stepMinMatch = /^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/.exec(cron)
    if (stepMinMatch) return `A cada ${stepMinMatch[1]} min`
    if (cron === '0 * * * *') return 'A cada hora'
    const parts = cron.split(/\s+/)
    if (parts.length === 5) {
      const [min, hour, dom, mon, dow] = parts
      const minNum = Number(min)
      if (/^\d+$/.test(min) && minNum >= 0 && minNum <= 59 && dom === '*' && mon === '*') {
        const hourTokens = hour.split(',')
        const allValidHours =
          hourTokens.length > 0 &&
          hourTokens.every((token) => /^\d+$/.test(token) && Number(token) >= 0 && Number(token) <= 23)
        if (allValidHours) {
          const times = hourTokens
            .map((token) => `${String(Number(token)).padStart(2, '0')}:${String(minNum).padStart(2, '0')}`)
            .join(', ')
          if (dow === '*') return `Todos os dias · ${times}`
          const dowLabel = WEEKDAY_CRON_LABELS[dow]
          if (dowLabel) return `${dowLabel} · ${times}`
        }
      }
    }
    return `Cron · ${cron}`
  }
  if (typeof input === 'object') {
    if (input.recurrence === 'hourly') return 'A cada hora'
    if (input.recurrence === 'daily') {
      return `Todos os dias · ${input.time_of_day || '09:00'}`
    }
    if (input.recurrence === 'weekly') {
      const dayLabel =
        typeof input.weekday === 'number' && input.weekday >= 0 && input.weekday <= 6
          ? WEEKDAY_INDEX_LABELS[input.weekday]
          : 'Toda semana'
      return `${dayLabel} · ${input.time_of_day || '09:00'}`
    }
    if (input.recurrence === 'once') return 'Uma vez'
  }
  return 'Uma vez'
}

export const formatHumanWhen = formatHumanCron

export function createScheduledChat(
  client: ApiClient,
  input: {
    message: string
    provider: ProviderName | string
    model_id: string
    timezone: string
    recurrence: ScheduleRecurrence
    project_id: string | null
  },
  intent = client.createMutationIntent(),
): Promise<Pick<ScheduledChat, 'schedule_id' | 'state' | 'next_fire_at' | 'recurrence'>> {
  return client.request({
    path: '/v1/schedules',
    method: 'POST',
    expectedStatus: 201,
    intent,
    body: {
      message: input.message,
      selection: { provider: input.provider, model_id: input.model_id },
      timezone: input.timezone,
      recurrence: input.recurrence,
      project_id: input.project_id,
    },
    parse: schedule,
  })
}

export function listScheduledChats(client: ApiClient): Promise<{ items: ScheduledChat[] }> {
  return client.request({
    path: '/v1/schedules',
    parse: (value) => {
      const data = record(value)
      if (!Array.isArray(data.items)) throw invalidResponseError()
      return { items: data.items.map(schedule) }
    },
  })
}

export function updateScheduledChat(
  client: ApiClient,
  scheduleId: string,
  input: UpdateScheduledChatInput,
  intent = client.createMutationIntent(),
): Promise<ScheduledChat> {
  const body: Record<string, unknown> = {}
  if (input.message !== undefined) body.message = input.message
  if (input.provider !== undefined && input.model_id !== undefined) {
    body.selection = { provider: input.provider, model_id: input.model_id }
  }
  if (input.timezone !== undefined) body.timezone = input.timezone
  if (input.recurrence !== undefined) body.recurrence = input.recurrence
  if (input.project_id !== undefined) body.project_id = input.project_id
  if (input.state !== undefined) body.state = input.state

  return client.request({
    path: `/v1/schedules/${encodeURIComponent(scheduleId)}`,
    method: 'PATCH',
    expectedStatus: 200,
    intent,
    body,
    parse: schedule,
  })
}

export function cancelScheduledChat(
  client: ApiClient,
  scheduleId: string,
  intent = client.createMutationIntent(),
): Promise<void> {
  return client.request({
    path: `/v1/schedules/${encodeURIComponent(scheduleId)}`,
    method: 'DELETE',
    expectedStatus: 204,
    intent,
    parse: () => undefined,
  })
}

export function deleteScheduledChat(
  client: ApiClient,
  scheduleId: string,
  intent = client.createMutationIntent(),
): Promise<void> {
  return client.request({
    path: `/v1/schedules/${encodeURIComponent(scheduleId)}`,
    query: { permanent: true },
    method: 'DELETE',
    expectedStatus: 204,
    intent,
    parse: () => undefined,
  })
}

function schedule(value: unknown): ScheduledChat {
  const data = record(value)
  return {
    schedule_id: text(data.schedule_id),
    state: text(data.state),
    next_fire_at: nullable(data.next_fire_at),
    recurrence: recurrence(data.recurrence),
    project_id: nullable(data.project_id),
    conversation_project_id: nullable(data.conversation_project_id),
    conversation_title: nullable(data.conversation_title),
    message: typeof data.message === 'string' ? data.message : '',
    provider: typeof data.provider === 'string' ? data.provider : '',
    model_id: typeof data.model_id === 'string' ? data.model_id : '',
    model_display_name: nullable(data.model_display_name),
    conversation_id: nullable(data.conversation_id),
    cron: typeof data.cron === 'string' ? data.cron.slice(0, 120) : null,
    time_of_day: nullable(data.time_of_day),
    weekday: typeof data.weekday === 'number' ? data.weekday : null,
    fire_at: nullable(data.fire_at),
    byAgent: typeof data.source === 'string' && data.source !== 'user',
    timezone: typeof data.timezone === 'string' && data.timezone ? data.timezone : 'America/Sao_Paulo',
    ends_at: nullable(data.ends_at),
    created_at: nullable(data.created_at),
    updated_at: nullable(data.updated_at),
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw invalidResponseError()
  return value
}

function nullable(value: unknown): string | null {
  return value === null || value === undefined || value === '' ? null : text(value)
}

function recurrence(value: unknown): ScheduledChat['recurrence'] {
  if (value === 'once' || value === 'hourly' || value === 'daily' || value === 'weekly' || value === 'cron') return value
  throw invalidResponseError()
}
