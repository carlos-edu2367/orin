import type { ConversationActivityEvent } from './activityTypes'

/** What the Bancada Preview tab shows for the agent's local dev server. */
export type CodeWorkbenchPreviewState = {
  status: 'starting' | 'running' | 'failed'
  url?: string | null
  name?: string | null
  error?: string | null
  /** The dev server hot-reloads by itself (Vite, Next, webpack-dev-server...). */
  hmr?: boolean | null
  /** Changes on every PreviewStart/PreviewOpen outcome: the panel opens on Preview and navigates. */
  token: string
}

const START_TOOLS = new Set(['preview_start', 'previewstart'])
const OPEN_TOOLS = new Set(['preview_open', 'previewopen'])
const STOP_TOOLS = new Set(['preview_stop', 'previewstop'])
const WRITE_TOOLS = new Set(['write_file', 'edit_file', 'create_file', 'notebook_edit', 'write', 'edit', 'notebookedit'])

function toolNameOf(event: ConversationActivityEvent): string {
  return (event.toolName ?? event.tool ?? '').toLowerCase()
}

function isFailed(event: ConversationActivityEvent): boolean {
  return event.state === 'failed' || event.status === 'failed' || Boolean(event.errorCode)
}

/**
 * Latest preview state reported by PreviewStart / PreviewOpen / PreviewStop activity events.
 * Returns undefined when no preview tool ran, null when the last one stopped the server.
 */
export function derivePreviewFromEvents(
  events: readonly ConversationActivityEvent[],
): CodeWorkbenchPreviewState | null | undefined {
  let state: CodeWorkbenchPreviewState | null | undefined
  for (const event of events) {
    const name = toolNameOf(event)
    const isStart = START_TOOLS.has(name)
    const isOpen = OPEN_TOOLS.has(name)
    if (STOP_TOOLS.has(name)) {
      if (event.type === 'tool.finished' && !isFailed(event)) state = null
      continue
    }
    if (!isStart && !isOpen) continue
    // Permission prompts for the tool are not a server state.
    if (event.toolPermission) continue
    const server = event.previewServer
    if (event.type === 'tool.started' || (event.type === 'tool.progressed' && !server)) {
      if (isStart) {
        state = { status: 'starting', name: server?.name ?? state?.name ?? null, url: state?.url ?? null, hmr: state?.hmr ?? null, token: event.eventId }
      }
      continue
    }
    if (event.type !== 'tool.finished' && !server) continue
    if (isFailed(event)) {
      state = {
        status: 'failed',
        name: server?.name ?? state?.name ?? null,
        url: server?.url ?? null,
        error: (event.content ?? event.summary ?? '').trim() || 'Falha ao abrir a prévia.',
        token: event.eventId,
      }
      continue
    }
    if (!server?.url) continue
    if (server.status === 'exited' || server.status === 'stopped') {
      state = {
        status: 'failed',
        name: server.name ?? null,
        url: server.url,
        hmr: server.hmr ?? null,
        error:
          server.status === 'exited'
            ? `O servidor ${server.name ?? ''} encerrou${typeof server.exitCode === 'number' ? ` (código ${server.exitCode})` : ''} antes de responder.`.replace(/\s+/g, ' ')
            : 'O servidor de prévia foi encerrado.',
        token: event.eventId,
      }
      continue
    }
    state = { status: 'running', url: server.url, name: server.name ?? null, hmr: server.hmr ?? null, token: event.eventId }
  }
  return state
}

/** Id of the latest successful write/edit tool result, used to auto-reload the preview. */
export function deriveFileChangeToken(events: readonly ConversationActivityEvent[]): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'code_mode.file_edited' && !isFailed(event)) return event.eventId
    if (event.type !== 'tool.finished' || isFailed(event) || event.unchanged) continue
    if (WRITE_TOOLS.has(toolNameOf(event))) return event.eventId
  }
  return null
}

/** Ports where the usual dev server already hot-reloads (Vite, Next/CRA, Angular, Astro, webpack-dev-server). */
const HMR_PORTS = new Set([5173, 5174, 5175, 5176, 3000, 3001, 4200, 4321, 8080])

export function isLikelyHmrUrl(url: string | null | undefined): boolean {
  if (!url) return false
  try {
    const parsed = new URL(url)
    const port = parsed.port ? Number(parsed.port) : NaN
    return HMR_PORTS.has(port)
  } catch {
    return false
  }
}
