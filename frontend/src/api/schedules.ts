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
  message: string
  provider: string
  model_id: string
  conversation_id: string | null
  cron: string | null
  byAgent: boolean
  timezone?: string
  paused?: boolean
  projectLabel?: string
}

export type ScheduleItem = ScheduledChat

export function formatHumanCron(
  input: string | Pick<ScheduledChat, 'recurrence' | 'cron' | 'next_fire_at'> | null | undefined,
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
      const hourNum = Number(hour)
      if (
        /^\d+$/.test(min) &&
        /^\d+$/.test(hour) &&
        minNum >= 0 &&
        minNum <= 59 &&
        hourNum >= 0 &&
        hourNum <= 23 &&
        dom === '*' &&
        mon === '*'
      ) {
        const timeStr = `${String(hourNum).padStart(2, '0')}:${String(minNum).padStart(2, '0')}`
        if (dow === '1-5') return `Seg a sex · ${timeStr}`
        if (dow === '5') return `Sexta · ${timeStr}`
        if (dow === '1') return `Segunda · ${timeStr}`
        if (dow === '*') return `Todos os dias · ${timeStr}`
      }
    }
    return `Cron · ${cron}`
  }
  if (typeof input === 'object') {
    if (input.recurrence === 'hourly') return 'A cada hora'
    if (input.recurrence === 'daily') return 'Todos os dias · 09:00'
    if (input.recurrence === 'weekly') return 'Toda semana · 09:00'
    if (input.recurrence === 'once') return 'Uma vez'
  }
  return 'Uma vez'
}

export const formatHumanWhen = formatHumanCron

export function createScheduledChat(
  client: ApiClient,
  input: {
    message: string
    provider: ProviderName
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

function schedule(value: unknown): ScheduledChat {
  const data = record(value)
  return {
    schedule_id: text(data.schedule_id),
    state: text(data.state),
    next_fire_at: nullable(data.next_fire_at),
    recurrence: recurrence(data.recurrence),
    project_id: nullable(data.project_id),
    message: typeof data.message === 'string' ? data.message : '',
    provider: typeof data.provider === 'string' ? data.provider : '',
    model_id: typeof data.model_id === 'string' ? data.model_id : '',
    conversation_id: nullable(data.conversation_id),
    cron: typeof data.cron === 'string' ? data.cron.slice(0, 120) : null,
    byAgent: typeof data.source === 'string' && data.source !== 'user',
    timezone: typeof data.timezone === 'string' && data.timezone ? data.timezone : 'America/Sao_Paulo',
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
  return value === null || value === undefined ? null : text(value)
}

function recurrence(value: unknown): ScheduledChat['recurrence'] {
  if (value === 'once' || value === 'hourly' || value === 'daily' || value === 'weekly' || value === 'cron') return value
  throw invalidResponseError()
}
