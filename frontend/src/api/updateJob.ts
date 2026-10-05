import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type UpdateJobState =
  | 'idle' | 'checking' | 'downloading' | 'verifying' | 'extracting' | 'validating'
  | 'ready' | 'up_to_date' | 'failed' | 'unsupported'

export type UpdateAttempt = { status: 'updated' | 'rolled_back'; version: string; at?: string; message?: string; restored?: string }

export type UpdateJobStatus = {
  state: UpdateJobState
  current_version: string
  version: string | null
  progress: number | null
  bytes_done: number | null
  bytes_total: number | null
  speed: number | null
  label: string | null
  notes: string | null
  error: { message: string; hint: string | null } | null
  last_attempt: UpdateAttempt | null
}

const STATES: readonly string[] = ['idle', 'checking', 'downloading', 'verifying', 'extracting', 'validating', 'ready', 'up_to_date', 'failed', 'unsupported']
/** States in which the backend is still working, so the page should keep polling. */
export const ACTIVE_UPDATE_STATES: readonly UpdateJobState[] = ['checking', 'downloading', 'verifying', 'extracting', 'validating']

const text = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const number = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

function parseStatus(value: unknown): UpdateJobStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (typeof data.state !== 'string' || !STATES.includes(data.state) || typeof data.current_version !== 'string') throw invalidResponseError()
  let error: UpdateJobStatus['error'] = null
  if (data.error && typeof data.error === 'object') {
    const raw = data.error as Record<string, unknown>
    error = { message: text(raw.message) ?? 'A atualização falhou.', hint: text(raw.hint) }
  }
  let attempt: UpdateAttempt | null = null
  if (data.last_attempt && typeof data.last_attempt === 'object') {
    const raw = data.last_attempt as Record<string, unknown>
    if ((raw.status === 'updated' || raw.status === 'rolled_back') && typeof raw.version === 'string') {
      attempt = { status: raw.status, version: raw.version, ...(text(raw.at) ? { at: text(raw.at) as string } : {}), ...(text(raw.message) ? { message: text(raw.message) as string } : {}), ...(text(raw.restored) ? { restored: text(raw.restored) as string } : {}) }
    }
  }
  return {
    state: data.state as UpdateJobState, current_version: data.current_version, version: text(data.version),
    progress: number(data.progress), bytes_done: number(data.bytes_done), bytes_total: number(data.bytes_total),
    speed: number(data.speed), label: text(data.label), notes: text(data.notes), error, last_attempt: attempt,
  }
}

export function getUpdateStatus(client: ApiClient, signal?: AbortSignal): Promise<UpdateJobStatus> {
  return client.request({ path: '/v1/installation/update/status', signal, parse: parseStatus })
}

/** Starts the background download; the status endpoint reports its progress. */
export function prepareUpdate(client: ApiClient, intent = client.createMutationIntent()): Promise<UpdateJobStatus> {
  return client.request({ path: '/v1/installation/update/prepare', method: 'POST', intent, parse: parseStatus })
}
