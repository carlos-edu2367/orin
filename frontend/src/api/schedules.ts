import type { ApiClient } from './client'
import { invalidResponseError } from './errors'
import type { ProviderName } from './providers'

export type ScheduleRecurrence = { kind: 'once'; fire_at: string } | { kind: 'hourly' } | { kind: 'daily'; time_of_day: string } | { kind: 'weekly'; time_of_day: string; weekday: number }
export type ScheduledChat = { schedule_id: string; state: string; next_fire_at: string | null; recurrence: ScheduleRecurrence['kind']; project_id: string | null; message: string; provider: string; model_id: string; conversation_id: string | null }

export function createScheduledChat(client: ApiClient, input: { message: string; provider: ProviderName; model_id: string; timezone: string; recurrence: ScheduleRecurrence; project_id: string | null }, intent = client.createMutationIntent()): Promise<Pick<ScheduledChat, 'schedule_id' | 'state' | 'next_fire_at' | 'recurrence'>> {
  return client.request({ path: '/v1/schedules', method: 'POST', expectedStatus: 201, intent, body: { message: input.message, selection: { provider: input.provider, model_id: input.model_id }, timezone: input.timezone, recurrence: input.recurrence, project_id: input.project_id }, parse: schedule })
}
export function listScheduledChats(client: ApiClient): Promise<{ items: ScheduledChat[] }> { return client.request({ path: '/v1/schedules', parse: (value) => { const data = record(value); if (!Array.isArray(data.items)) throw invalidResponseError(); return { items: data.items.map(schedule) } } }) }
export function cancelScheduledChat(client: ApiClient, scheduleId: string, intent = client.createMutationIntent()): Promise<void> { return client.request({ path: `/v1/schedules/${encodeURIComponent(scheduleId)}`, method: 'DELETE', expectedStatus: 204, intent, parse: () => undefined }) }
function schedule(value: unknown): ScheduledChat { const data = record(value); return { schedule_id: text(data.schedule_id), state: text(data.state), next_fire_at: nullable(data.next_fire_at), recurrence: recurrence(data.recurrence), project_id: nullable(data.project_id), message: typeof data.message === 'string' ? data.message : '', provider: typeof data.provider === 'string' ? data.provider : '', model_id: typeof data.model_id === 'string' ? data.model_id : '', conversation_id: nullable(data.conversation_id) } }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError(); return value as Record<string, unknown> }
function text(value: unknown): string { if (typeof value !== 'string' || !value) throw invalidResponseError(); return value }
function nullable(value: unknown): string | null { return value === null || value === undefined ? null : text(value) }
function recurrence(value: unknown): ScheduledChat['recurrence'] { if (value === 'once' || value === 'hourly' || value === 'daily' || value === 'weekly') return value; throw invalidResponseError() }
