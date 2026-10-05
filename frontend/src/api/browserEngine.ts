import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type BrowserEngineState = 'ready' | 'missing' | 'installing' | 'failed' | 'unsupported'
export type BrowserEngineStatus = {
  state: BrowserEngineState
  progress: number | null
  message: string | null
  error: string | null
  install_command: string
}

const STATES: readonly string[] = ['ready', 'missing', 'installing', 'failed', 'unsupported']

function parseStatus(value: unknown): BrowserEngineStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (typeof data.state !== 'string' || !STATES.includes(data.state)) throw invalidResponseError()
  return {
    state: data.state as BrowserEngineState,
    progress: typeof data.progress === 'number' ? data.progress : null,
    message: typeof data.message === 'string' ? data.message : null,
    error: typeof data.error === 'string' ? data.error : null,
    install_command: typeof data.install_command === 'string' ? data.install_command : 'orin browser install',
  }
}

export function getBrowserEngineStatus(client: ApiClient, signal?: AbortSignal): Promise<BrowserEngineStatus> {
  return client.request({ path: '/v1/runtime/browser', signal, parse: parseStatus })
}

/** Starts the download in the background; the status endpoint reports its progress. */
export function installBrowserEngine(client: ApiClient, intent = client.createMutationIntent()): Promise<BrowserEngineStatus> {
  return client.request({ path: '/v1/runtime/browser/install', method: 'POST', intent, parse: parseStatus })
}
