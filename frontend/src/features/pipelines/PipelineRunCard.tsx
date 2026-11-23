import { createContext, useContext, useMemo } from 'react'
import { Link } from 'react-router-dom'
import type { ActivityGroup } from '../conversations/activityTypes'
import { formatCost, formatDuration, formatTokens, RUN_STATE_LABEL, stopReasonText, toneOf } from './format'
import {
  projectPipelineEvents,
  STEP_STATE_LABEL,
  type PipelineProjection,
  type PipelineRunModel,
  type StepModel,
} from './pipelineProjection'
import './pipelines.css'

/** The conversation-wide projection (the reducer's `pipelines`): a resumed run keeps the steps of the earlier turn. */
export const PipelineProjectionContext = createContext<PipelineProjection>({})

function itemSummary(step: StepModel): string | null {
  const parts: string[] = []
  const latest = step.items.reduce((highest, item) => Math.max(highest, item.loopCount), 0)
  const visit = step.items.filter((item) => item.loopCount === latest)
  if (visit.length > 1) {
    const done = visit.filter((item) => item.state === 'succeeded' || item.state === 'reused').length
    parts.push(`${done} de ${visit.length} itens`)
  }
  if (step.loop && step.loop.count > 0) parts.push(step.loop.max ? `volta ${step.loop.count} de ${step.loop.max}` : `volta ${step.loop.count}`)
  const retrying = visit.find((item) => item.retry)
  if (retrying?.retry) parts.push(`nova tentativa ${retrying.retry.nextAttempt}`)
  if (step.reusedItems > 0 && step.reusedItems === step.items.length) parts.push('reaproveitada do diário')
  return parts.length ? parts.join(' · ') : null
}

export function PipelineStepLine({ step }: { step: StepModel }) {
  const tone = toneOf(step.state)
  const detail = itemSummary(step)
  const tokens = step.inputTokens + step.outputTokens
  return (
    <li className="pipeline-steps__row" data-state={step.state} data-testid={`pipeline-step-${step.stepId}`}>
      <span className={`pipeline-dot pipeline-dot--${tone}`} aria-hidden="true" />
      <span className="pipeline-steps__name">{step.stepId}</span>
      <span className="pipeline-steps__state">{STEP_STATE_LABEL[step.state]}{detail ? ` · ${detail}` : ''}</span>
      <span className="pipeline-steps__tokens">{tokens > 0 ? `${formatTokens(tokens)} tokens` : ''}</span>
    </li>
  )
}

export function RunTotals({ run }: { run: PipelineRunModel }) {
  return (
    <p className="pipeline-card__totals" data-testid="pipeline-totals">
      <span>{formatTokens(run.usedTokens)} tokens</span>
      <span>{formatCost(run.usedCostUsd, run.costKnown)}</span>
      {run.durationMs !== null && <span>{formatDuration(run.durationMs)}</span>}
    </p>
  )
}

/** The compact card in the conversation timeline: one per run, steps in the order they ran, a link to the full screen. */
export function PipelineRunCard({ group }: { group: ActivityGroup }) {
  const projection = useContext(PipelineProjectionContext)
  const runId = String(group.events[0]?.payload?.run_id ?? '')
  const local = useMemo(() => projectPipelineEvents(group.events), [group.events])
  const run = projection[runId] ?? local[runId]
  if (!run) return null
  const stateText = RUN_STATE_LABEL[run.state] ?? run.state
  const stop = run.state === 'running' ? null : stopReasonText(run.stopReason)
  return (
    <article className="pipeline-card" data-testid="pipeline-run-card" data-state={run.state} aria-label={`Pipeline ${run.pipelineId || run.runId}`}>
      <header className="pipeline-card__head">
        <div className="pipeline-card__title">
          <h4>Pipeline <code>{run.pipelineId || run.runId}</code></h4>
          {run.stepsTotal > 0 && <span className="pipeline-card__muted">{run.stepsTotal} {run.stepsTotal === 1 ? 'etapa' : 'etapas'}</span>}
        </div>
        <span className={`pipeline-badge pipeline-badge--${toneOf(run.state)}`}>{stateText}</span>
      </header>
      {run.steps.length > 0 && (
        <ul className="pipeline-steps" aria-label="Etapas do pipeline">
          {run.steps.map((step) => <PipelineStepLine key={step.stepId} step={step} />)}
        </ul>
      )}
      <RunTotals run={run} />
      {run.resumed && (
        <p className="pipeline-card__note">
          Retomado: {run.resumed.reusedSteps} {run.resumed.reusedSteps === 1 ? 'etapa reaproveitada' : 'etapas reaproveitadas'}, {run.resumed.pendingSteps} pendentes.
          {run.resumed.definitionChanged ? ' A definição do pipeline mudou desde o início.' : ''}
        </p>
      )}
      {run.degraded.map((note) => (
        <p key={`${note.reason}-${note.detail}`} className="pipeline-card__note pipeline-card__note--warn" role="note">
          Degradado: {note.detail || note.reason}{note.effect ? ` — ${note.effect}` : ''}
        </p>
      ))}
      {run.budgetExceeded && (
        <p className="pipeline-card__note pipeline-card__note--warn" role="note">
          Orçamento esgotado ({run.budgetExceeded.scope === 'pipeline' ? 'do pipeline' : run.budgetExceeded.scope}
          {run.budgetExceeded.limitKind ? `: ${run.budgetExceeded.limitKind.replaceAll('_', ' ')}` : ''}).
        </p>
      )}
      {stop && <p className="pipeline-card__note">Parou porque: {stop}.</p>}
      <footer className="pipeline-card__foot">
        <Link className="pipeline-link" to={`/pipelines/runs/${encodeURIComponent(run.runId)}`}>Abrir execução</Link>
      </footer>
    </article>
  )
}
