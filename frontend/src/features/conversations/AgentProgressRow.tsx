import { useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { resumeSubagent, type ResumeSubagentResponse } from '../../api/conversations'
import { formatElapsed } from './activitySummary'
import type { AgentMergeState, AgentProgressData } from './agentTree'
import './AgentProgressRow.css'

export type AgentProgressRowProps = {
  progress: AgentProgressData
  conversationId?: string
  client?: ApiClient
  compact?: boolean
  onResumed?: (agentId: string, response: ResumeSubagentResponse) => void
  onResume?: (conversationId: string, agentId: string) => Promise<ResumeSubagentResponse>
}

const MERGE_STATE_LABELS: Record<AgentMergeState, string> = {
  pending: 'worktree pendente',
  applied: 'integrado',
  conflict: 'conflito de integração',
  check_failed: 'checagem falhou',
  discarded: 'descartado',
  empty: 'sem alterações',
}

function formatTokenTotal(inputTokens: number, outputTokens: number): string {
  const total = Math.max(0, inputTokens) + Math.max(0, outputTokens)
  return `${total.toLocaleString('pt-BR')} tokens`
}

function formatCost(costUsd: number): string {
  if (costUsd < 0.01) return `$${costUsd.toFixed(4)}`
  return `$${costUsd.toFixed(4)}`
}

export function AgentProgressRow({
  progress,
  conversationId,
  client,
  compact = false,
  onResumed,
  onResume,
}: AgentProgressRowProps) {
  const [resumeStatus, setResumeStatus] = useState<'idle' | 'working' | 'resumed' | 'failed'>('idle')

  const isStalled = progress.stalled || progress.state === 'stalled'
  const isInterrupted =
    (progress.interrupted || progress.state === 'interrupted') && resumeStatus !== 'resumed'
  const totalTokens = progress.inputTokens + progress.outputTokens
  const hasCost = progress.costUsd !== null && progress.costUsd > 0

  async function handleResume() {
    if (!conversationId && !onResume) return
    if (resumeStatus === 'working' || resumeStatus === 'resumed') return
    setResumeStatus('working')
    try {
      const response = onResume
        ? await onResume(conversationId ?? '', progress.agentId)
        : await resumeSubagent(client ?? createBrowserApiClient(), conversationId as string, progress.agentId)
      setResumeStatus('resumed')
      onResumed?.(progress.agentId, response)
    } catch {
      setResumeStatus('failed')
    }
  }

  return (
    <div
      className={`agent-progress-row${compact ? ' agent-progress-row--compact' : ''}`}
      data-testid={`agent-progress-${progress.agentId}`}
      data-state={resumeStatus === 'resumed' ? 'queued' : progress.state}
    >
      <div className="agent-progress-row__metrics">
        {isStalled && (
          <span
            className="agent-progress-row__badge agent-progress-row__badge--stalled"
            data-testid="agent-stalled-badge"
          >
            travado
          </span>
        )}
        {isInterrupted && (
          <span
            className="agent-progress-row__badge agent-progress-row__badge--interrupted"
            data-testid="agent-interrupted-badge"
          >
            interrompido
          </span>
        )}
        {resumeStatus === 'resumed' && (
          <span
            className="agent-progress-row__badge agent-progress-row__badge--resumed"
            data-testid="agent-resumed-badge"
          >
            retomado
          </span>
        )}
        {progress.currentTool && (
          <span className="agent-progress-row__chip" title={`Ferramenta atual: ${progress.currentTool}`}>
            <code>{progress.currentTool}</code>
          </span>
        )}
        {progress.toolCalls > 0 && (
          <span className="agent-progress-row__metric">
            {progress.toolCalls} {progress.toolCalls === 1 ? 'ferramenta' : 'ferramentas'}
          </span>
        )}
        {progress.elapsedSeconds > 0 && (
          <span className="agent-progress-row__metric">{formatElapsed(progress.elapsedSeconds)}</span>
        )}
        {totalTokens > 0 && (
          <span
            className="agent-progress-row__metric"
            title={`Entrada: ${progress.inputTokens.toLocaleString('pt-BR')} · Saída: ${progress.outputTokens.toLocaleString('pt-BR')}`}
          >
            {formatTokenTotal(progress.inputTokens, progress.outputTokens)}
          </span>
        )}
        {hasCost && progress.costUsd !== null && (
          <span className="agent-progress-row__metric">{formatCost(progress.costUsd)}</span>
        )}
        {progress.mergeState && (
          <span
            className={`agent-progress-row__badge agent-progress-row__badge--merge-${progress.mergeState}`}
            data-testid="agent-merge-state-badge"
          >
            {MERGE_STATE_LABELS[progress.mergeState]}
          </span>
        )}
      </div>

      {isInterrupted && (conversationId || onResume) && (
        <div className="agent-progress-row__actions">
          <button
            type="button"
            className="ghost-button agent-progress-row__resume"
            data-testid={`resume-agent-${progress.agentId}`}
            disabled={resumeStatus === 'working'}
            onClick={() => void handleResume()}
          >
            {resumeStatus === 'working' ? 'Retomando…' : 'Retomar'}
          </button>
          {resumeStatus === 'failed' && (
            <span className="agent-progress-row__error" role="alert">
              Não foi possível retomar o agente.
            </span>
          )}
        </div>
      )}
    </div>
  )
}
