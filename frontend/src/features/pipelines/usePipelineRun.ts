import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import {
  estimatePipeline,
  getPipeline,
  getPipelineRun,
  type PipelineBudget,
  type PipelineDetail,
  type PipelineRunDetail,
} from '../../api/pipelines'

const LIVE_POLL_MS = 2_000
const IDLE_POLL_MS = 15_000

export type PipelineRunData = {
  run: PipelineRunDetail | null
  /** The definition the run came from, for the order and names of the steps; null when the file is gone or changed. */
  definition: PipelineDetail | null
  /** The run's effective ceiling, best effort (the run answer does not carry it). */
  budget: PipelineBudget | null
  error: string | null
  loading: boolean
  refresh: () => Promise<void>
}

/**
 * Reads one pipeline run and keeps it fresh: every 2 s while it runs, every 15 s otherwise (a run can be resumed from
 * another window). The step order comes from the definition; if the file changed since the run, the journal order is used.
 * Mount it under `key={runId}` (as `PipelineRunPage` does): another run is a fresh state, not a reset inside an effect.
 */
export function usePipelineRun(client: ApiClient, runId: string): PipelineRunData {
  const [run, setRun] = useState<PipelineRunDetail | null>(null)
  const [definition, setDefinition] = useState<PipelineDetail | null>(null)
  const [budget, setBudget] = useState<PipelineBudget | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadedFor = useRef<string | null>(null)
  const alive = useRef(true)

  // Every state change happens in a promise callback, never synchronously in the caller (the effects below call it).
  const refresh = useCallback(
    (): Promise<void> =>
      getPipelineRun(client, runId)
        .then((next) => {
          if (!alive.current) return
          setRun(next)
          setError(null)
          const key = `${next.pipelineId}:${next.conversationId}`
          if (loadedFor.current === key) return
          loadedFor.current = key
          getPipeline(client, next.pipelineId, next.conversationId)
            .then((detail) => { if (alive.current) setDefinition(detail.definitionHash === next.definitionHash ? detail : null) })
            .catch(() => { if (alive.current) setDefinition(null) })
          estimatePipeline(client, next.pipelineId, next.inputs, next.conversationId)
            .then((estimate) => { if (alive.current) setBudget(estimate.budget) })
            .catch(() => { if (alive.current) setBudget(null) })
        })
        .catch((caught: unknown) => {
          if (!alive.current) return
          setError(caught instanceof ApiError && caught.status === 404 ? 'Esta execução não existe (ou é de outra conta).' : 'Não foi possível ler a execução agora.')
        })
        .finally(() => { if (alive.current) setLoading(false) }),
    [client, runId],
  )

  useEffect(() => {
    alive.current = true
    loadedFor.current = null
    void refresh()
    return () => { alive.current = false }
  }, [refresh])

  const state = run?.state ?? null
  useEffect(() => {
    if (state === null) return undefined
    const timer = window.setInterval(() => { void refresh() }, state === 'running' ? LIVE_POLL_MS : IDLE_POLL_MS)
    return () => window.clearInterval(timer)
  }, [state, refresh])

  return { run, definition, budget, error, loading, refresh }
}

/** The `max_loops` written for a step in the YAML text, when it can be read without guessing; null otherwise. */
export function maxLoopsFromText(text: string, stepId: string): number | null {
  const blocks = text.split(/^\s*-\s+id:\s*/m)
  for (const block of blocks.slice(1)) {
    const id = /^["']?([a-z0-9][a-z0-9_-]*)["']?/i.exec(block)?.[1]
    if (id !== stepId) continue
    const match = /max_loops:\s*(\d+)/.exec(block)
    return match ? Number(match[1]) : null
  }
  return null
}
