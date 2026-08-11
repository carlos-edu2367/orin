import { ApiClient } from '../../api/client'
import { ApiError, shouldResync } from '../../api/errors'
import { getExecution } from '../../api/executions'
import { openEventStream, readEventStream } from '../../api/events'
import { projectExecution, type ExecutionProjection } from '../executions/executionProjection'
import {
  applyEventBatch,
  ProjectionInconsistencyError,
  resetDeliverySequences,
  type StreamState,
} from './eventReducer'

export type RealtimeConnectionState = 'idle' | 'connecting' | 'live' | 'recovering' | 'resyncing' | 'degraded'

export type RealtimeSnapshot = StreamState & {
  connection: RealtimeConnectionState
  appliedEventCount: number
  errorMessageKey: string | null
}

export function createEmptyRealtimeSnapshot(): RealtimeSnapshot {
  return {
    cursor: null,
    seenEventIds: [],
    executions: {} as Record<string, ExecutionProjection>,
    connection: 'idle',
    appliedEventCount: 0,
    errorMessageKey: null,
  }
}

type RealtimeStoreOptions = {
  client: ApiClient
  executionId: string
  pollDelayMs?: number
  recoveryDelayMs?: number
  maxRecoveryAttempts?: number
}

type Listener = () => void

export class ExecutionRealtimeStore {
  private readonly client: ApiClient
  private readonly executionId: string
  private readonly pollDelayMs: number
  private readonly recoveryDelayMs: number
  private readonly maxRecoveryAttempts: number
  private readonly listeners = new Set<Listener>()
  private snapshot = createEmptyRealtimeSnapshot()
  private active = false
  private generation = 0
  private recoveryAttempts = 0
  private abortController: AbortController | null = null

  constructor(options: RealtimeStoreOptions) {
    this.client = options.client
    this.executionId = options.executionId
    this.pollDelayMs = Math.max(20, options.pollDelayMs ?? 120)
    this.recoveryDelayMs = Math.max(20, options.recoveryDelayMs ?? 120)
    this.maxRecoveryAttempts = Math.max(1, Math.min(options.maxRecoveryAttempts ?? 3, 5))
  }

  getSnapshot = (): RealtimeSnapshot => this.snapshot

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  start(): void {
    if (this.active) return
    this.active = true
    const generation = ++this.generation
    this.abortController = new AbortController()
    void this.connect(generation, 'connecting')
  }

  stop(): void {
    this.active = false
    this.generation += 1
    this.abortController?.abort()
    this.abortController = null
  }

  resync(): void {
    if (!this.active) return
    this.abortController?.abort()
    const generation = ++this.generation
    this.abortController = new AbortController()
    this.recoveryAttempts = 0
    this.publish({ ...this.snapshot, cursor: null, connection: 'resyncing', errorMessageKey: null })
    void this.connect(generation, 'resyncing')
  }

  private async connect(generation: number, connection: RealtimeConnectionState): Promise<void> {
    if (!this.isCurrent(generation)) return
    this.publish({ ...this.snapshot, connection, errorMessageKey: null })
    try {
      const signal = this.abortController?.signal
      const view = await getExecution(this.client, this.executionId, signal)
      if (!this.isCurrent(generation)) return
      const current = this.snapshot.executions[this.executionId]
      const projection = current && current.state_version > view.state_version ? current : projectExecution(view)
      this.publish({
        ...this.snapshot,
        cursor: null,
        executions: { ...this.snapshot.executions, [this.executionId]: projection },
        connection,
      })

      const binding = await openEventStream(this.client, [this.executionId], signal)
      if (!this.isCurrent(generation)) return
      this.publish({
        ...this.snapshot,
        ...resetDeliverySequences(this.snapshot),
        cursor: binding.cursor,
        connection: 'live',
        errorMessageKey: null,
      })
      await wait(this.pollDelayMs, signal)
      await this.poll(generation, binding.stream_id)
    } catch (error) {
      await this.recover(generation, error)
    }
  }

  private async poll(generation: number, streamId: string): Promise<void> {
    while (this.isCurrent(generation)) {
      try {
        const cursor = this.snapshot.cursor
        if (!cursor) throw new ProjectionInconsistencyError()
        const replay = await readEventStream(this.client, streamId, cursor, 100, this.abortController?.signal)
        if (!this.isCurrent(generation)) return

        // The cursor only advances once every event of the batch has been projected.
        const previous: RealtimeSnapshot = this.snapshot
        const projected = applyEventBatch(previous, replay.events, replay.cursor)
        this.recoveryAttempts = 0
        this.publish({
          ...this.snapshot,
          ...projected,
          connection: 'live',
          appliedEventCount: previous.appliedEventCount + countAppliedTransitions(previous, projected),
          errorMessageKey: null,
        })
        await wait(this.pollDelayMs, this.abortController?.signal)
      } catch (error) {
        await this.recover(generation, error)
        return
      }
    }
  }

  private async recover(generation: number, error: unknown): Promise<void> {
    if (!this.isCurrent(generation) || isAbort(error)) return
    this.recoveryAttempts += 1
    if (this.recoveryAttempts > this.maxRecoveryAttempts) {
      const errorMessageKey = error instanceof ApiError ? error.messageKey : 'realtime_unavailable'
      this.publish({ ...this.snapshot, cursor: null, connection: 'degraded', errorMessageKey })
      return
    }

    const connection: RealtimeConnectionState = error instanceof ProjectionInconsistencyError
      || (error instanceof ApiError && shouldResync(error))
      ? 'resyncing'
      : 'recovering'
    this.publish({ ...this.snapshot, cursor: null, connection, errorMessageKey: null })
    await wait(this.recoveryDelayMs * this.recoveryAttempts, this.abortController?.signal)
    if (this.isCurrent(generation)) await this.connect(generation, connection)
  }

  private isCurrent(generation: number): boolean {
    return this.active && generation === this.generation
  }

  private publish(snapshot: RealtimeSnapshot): void {
    this.snapshot = snapshot
    this.listeners.forEach((listener) => listener())
  }
}

/**
 * Counts transitions that the batch actually applied. Redelivered events keep the
 * count stable, so recovery never replays historical activity as if it were new.
 */
function countAppliedTransitions(previous: StreamState, next: StreamState): number {
  return Object.entries(next.executions).reduce((total, [executionId, projection]) => {
    const before = previous.executions[executionId]
    return before ? total + Math.max(0, projection.state_version - before.state_version) : total
  }, 0)
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(resolve, milliseconds)
    signal?.addEventListener('abort', () => {
      globalThis.clearTimeout(timeout)
      reject(new DOMException('The operation was aborted', 'AbortError'))
    }, { once: true })
  })
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
