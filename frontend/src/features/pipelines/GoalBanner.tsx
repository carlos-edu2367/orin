import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { clearGoal, getGoal, resumeGoal, startGoal, type GoalStatus } from '../../api/pipelines'
import { formatTokens, stopReasonText } from './format'
import { goalErrorText } from './pipelineCommands'
import './pipelines.css'

const POLL_MS = 10_000

const STATE_TEXT: Record<string, string> = {
  running: 'Objetivo em andamento',
  succeeded: 'O avaliador considerou o objetivo cumprido',
  interrupted: 'Objetivo parado',
  budget_exceeded: 'Objetivo parado no teto de tokens',
  failed: 'Objetivo parado',
}

/**
 * The `/goal` strip above the composer (0.13 WS-C11). It reads the objective from the API, shows what the evaluator
 * decided and why, and offers the three things a person may want: clear it, resume it (with a new token cap) or, when
 * the evaluator said "done" too early, continue anyway. It renders nothing when there is no objective.
 */
export function GoalBanner({ client, conversationId, refreshKey }: { client: ApiClient; conversationId: string; refreshKey?: string }) {
  const [goal, setGoal] = useState<GoalStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [capDraft, setCapDraft] = useState<{ key: string; value: string } | null>(null)
  const alive = useRef(true)

  // The cap field starts at the objective's own ceiling and keeps what the person typed for that same stop.
  const capKey = goal ? `${goal.runId}:${goal.state}:${goal.maxTokens}` : ''
  const cap = capDraft && capDraft.key === capKey ? capDraft.value : goal ? String(goal.maxTokens) : ''
  const setCap = (value: string) => setCapDraft({ key: capKey, value })

  const load = useCallback(
    () =>
      getGoal(client, conversationId)
        .then((next) => { if (alive.current) setGoal(next) })
        // The banner is a convenience: a failed read keeps what is on screen.
        .catch(() => undefined),
    [client, conversationId],
  )

  useEffect(() => {
    alive.current = true
    void load()
    return () => { alive.current = false }
  }, [load, refreshKey])

  const running = goal?.state === 'running'
  useEffect(() => {
    if (!running) return undefined
    const timer = window.setInterval(() => { void load() }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, load])

  async function act(work: () => Promise<GoalStatus | null | void>) {
    setBusy(true)
    setError(null)
    try {
      const result = await work()
      if (!alive.current) return
      if (result === undefined) await load()
      else setGoal(result)
    } catch (caught: unknown) {
      if (alive.current) setError(goalErrorText(caught))
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  if (!goal || goal.state === 'cancelled' || dismissed === goal.runId) return null

  const evaluation = goal.lastEvaluation
  const stopped = goal.state !== 'running' && goal.state !== 'succeeded'
  const parsedCap = Number(cap.replaceAll('.', ''))
  const capValid = /^\d+$/.test(cap.replaceAll('.', '')) && parsedCap >= 1000 && parsedCap <= 5_000_000

  function submitResume(event: FormEvent) {
    event.preventDefault()
    if (!goal || busy) return
    const wantsNewCap = goal.state === 'budget_exceeded' && capValid && parsedCap !== goal.maxTokens
    void act(() => resumeGoal(client, conversationId, wantsNewCap ? parsedCap : null))
  }

  return (
    <section className="goal-banner" data-testid="goal-banner" data-state={goal.state} aria-label="Objetivo do /goal">
      <header className="goal-banner__head">
        <span className="goal-banner__badge">{STATE_TEXT[goal.state] ?? 'Objetivo'}</span>
        <span className="goal-banner__objective" title={goal.objective}>{goal.objective}</span>
      </header>

      <dl className="goal-banner__facts">
        <div data-testid="goal-tokens">
          <dt>Tokens</dt>
          <dd>{formatTokens(goal.windowUsedTokens)} de {formatTokens(goal.maxTokens)} · restam {formatTokens(goal.remainingTokens)}</dd>
        </div>
        <div>
          <dt>Turnos</dt>
          <dd>{goal.turns}</dd>
        </div>
        {goal.evaluatorModel && (
          <div data-testid="goal-evaluator">
            <dt>Avaliador</dt>
            <dd>
              {goal.evaluatorModel}
              {evaluation?.fallback ? ' · modelo do turno em esforço baixo (custa mais que um modelo pequeno)' : ''}
            </dd>
          </div>
        )}
      </dl>

      {evaluation?.reason && (
        <p className="goal-banner__reason" data-testid="goal-reason">
          <strong>Última avaliação{evaluation.turnIndex !== null ? ` (turno ${evaluation.turnIndex})` : ''}:</strong> {evaluation.reason}
        </p>
      )}
      {stopped && goal.stopReason && <p className="goal-banner__stop">Parou porque: {stopReasonText(goal.stopReason)}.</p>}
      {error && <p className="goal-banner__error" role="alert">{error}</p>}

      <div className="goal-banner__actions">
        {stopped && goal.resumable && (
          <form className="goal-banner__resume" onSubmit={submitResume}>
            {goal.state === 'budget_exceeded' && (
              <label className="goal-banner__cap">
                <span>Novo teto de tokens</span>
                <input type="number" inputMode="numeric" min={1000} max={5000000} step={1000} value={cap} disabled={busy} onChange={(event) => setCap(event.target.value)} />
              </label>
            )}
            <button type="submit" className="button button--primary pipelines-btn" disabled={busy || (goal.state === 'budget_exceeded' && !capValid)}>
              Retomar
            </button>
          </form>
        )}
        {goal.state === 'succeeded' && (
          <>
            <button
              type="button"
              className="button button--secondary pipelines-btn"
              disabled={busy}
              onClick={() => void act(async () => { await startGoal(client, conversationId, goal.objective, goal.maxTokens); return undefined })}
            >
              Continuar mesmo assim
            </button>
            <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={() => setDismissed(goal.runId)}>
              Dispensar
            </button>
          </>
        )}
        {goal.state !== 'succeeded' && (
          <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={() => void act(() => clearGoal(client, conversationId, false))}>
            Limpar
          </button>
        )}
        {goal.state === 'running' && (
          <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={() => void act(() => clearGoal(client, conversationId, true))}>
            Limpar e parar o turno
          </button>
        )}
      </div>
    </section>
  )
}
