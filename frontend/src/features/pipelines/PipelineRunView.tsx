import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import {
  cancelPipelineRun,
  definitionChangedOf,
  getStepOutput,
  pausePipelineRun,
  resumePipelineRun,
  type DefinitionChangedInfo,
  type PipelineRunDetail,
  type PipelineStepRow,
  type StepOutput,
  type UsageRow,
} from '../../api/pipelines'
import { formatCost, formatSeconds, formatTokens, RUN_STATE_LABEL, stopReasonText, toneOf } from './format'
import { STEP_STATE_LABEL, type StepItemState } from './pipelineProjection'
import { maxLoopsFromText, usePipelineRun } from './usePipelineRun'
import './pipelines.css'

type StepView = {
  stepId: string
  name: string
  kind: 'agent' | 'gate' | null
  rows: PipelineStepRow[]
  /** Latest attempt of each item in the latest visit ("volta"). */
  latest: PipelineStepRow[]
  state: StepItemState
  loopCount: number
  maxLoops: number | null
  attempts: number
  inputTokens: number
  outputTokens: number
  costUsd: number | null
  costKnown: boolean
}

function latestOf(rows: readonly PipelineStepRow[]): PipelineStepRow[] {
  const loop = rows.reduce((highest, row) => Math.max(highest, row.loopCount), 0)
  const byItem = new Map<string, PipelineStepRow>()
  for (const row of rows.filter((entry) => entry.loopCount === loop)) {
    const current = byItem.get(row.itemKey)
    if (!current || row.attempt > current.attempt) byItem.set(row.itemKey, row)
  }
  return [...byItem.values()]
}

function aggregateState(latest: readonly PipelineStepRow[]): StepItemState {
  const states = latest.map((row) => row.state)
  if (states.includes('running')) return 'running'
  if (states.includes('gate_failed')) return 'gate_failed'
  if (states.includes('failed')) return 'failed'
  if (states.includes('stalled')) return 'stalled'
  if (states.includes('interrupted')) return 'interrupted'
  if (states.includes('pending')) return 'pending'
  if (states.length > 0 && states.every((state) => state === 'skipped')) return 'skipped'
  return states.length ? 'succeeded' : 'pending'
}

export function buildStepViews(
  run: PipelineRunDetail,
  order: ReadonlyArray<{ id: string; name: string; kind: 'agent' | 'gate' }> | null,
  yaml: string | null,
): StepView[] {
  const byStep = new Map<string, PipelineStepRow[]>()
  for (const row of run.steps) byStep.set(row.stepId, [...(byStep.get(row.stepId) ?? []), row])
  const ids = order ? order.map((step) => step.id) : [...byStep.keys()]
  for (const id of byStep.keys()) if (!ids.includes(id)) ids.push(id)
  return ids.map((stepId) => {
    const rows = byStep.get(stepId) ?? []
    const info = order?.find((step) => step.id === stepId) ?? null
    const latest = latestOf(rows)
    const costs = rows.filter((row) => row.costUsd !== null)
    return {
      stepId,
      name: info?.name ?? stepId,
      kind: info?.kind ?? null,
      rows,
      latest,
      state: aggregateState(latest),
      loopCount: latest.reduce((highest, row) => Math.max(highest, row.loopCount), 0),
      maxLoops: yaml ? maxLoopsFromText(yaml, stepId) : null,
      attempts: rows.reduce((highest, row) => Math.max(highest, row.attempt), 0),
      inputTokens: rows.reduce((sum, row) => sum + row.inputTokens, 0),
      outputTokens: rows.reduce((sum, row) => sum + row.outputTokens, 0),
      costUsd: costs.length ? costs.reduce((sum, row) => sum + (row.costUsd ?? 0), 0) : null,
      // A row that spent tokens without a price makes the step cost partial.
      costKnown: rows.every((row) => row.costUsd !== null || row.inputTokens + row.outputTokens === 0),
    }
  })
}

function itemLabel(itemKey: string): string {
  return itemKey ? itemKey.replace(/^(\d+)-.*/, 'item $1') : 'principal'
}

function OutputPanel({ client, runId, row, onClose }: { client: ApiClient; runId: string; row: PipelineStepRow; onClose: () => void }) {
  const [output, setOutput] = useState<StepOutput | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    getStepOutput(client, runId, row.stepId, { itemKey: row.itemKey, attempt: row.attempt })
      .then((next) => { if (!cancelled) setOutput(next) })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError && caught.status === 410 ? 'A saída desta etapa não está mais disponível.' : 'Não foi possível ler a saída.')
      })
    return () => { cancelled = true }
  }, [client, runId, row.stepId, row.itemKey, row.attempt])
  const body = output ? (output.truncated ? output.preview ?? '' : typeof output.output === 'string' ? output.output : JSON.stringify(output.output, null, 2)) : ''
  return (
    <div className="pipeline-output" data-testid="step-output">
      <div className="pipeline-output__bar">
        <span>Saída de <code>{row.stepId}</code>{row.itemKey ? ` (${itemLabel(row.itemKey)})` : ''}, tentativa {row.attempt}</span>
        <button type="button" className="pipeline-link-button" onClick={onClose}>Fechar</button>
      </div>
      {error && <p className="pipeline-note pipeline-note--warn" role="status">{error}</p>}
      {!output && !error && <p className="pipeline-muted" role="status">Lendo a saída…</p>}
      {output?.truncated && <p className="pipeline-note pipeline-note--warn">A saída passa de 256 KB: só o começo aparece aqui.</p>}
      {output && <pre className="pipeline-output__body" tabIndex={0}>{body.length > 40_000 ? `${body.slice(0, 40_000)}\n… (cortado)` : body}</pre>}
    </div>
  )
}

function StepRowView({ view, onOpenOutput }: { view: StepView; onOpenOutput: (row: PipelineStepRow | null) => void }) {
  const tone = toneOf(view.state)
  const multi = view.latest.length > 1 || view.rows.length > view.latest.length
  const tokens = view.inputTokens + view.outputTokens
  const parts: string[] = []
  if (view.latest.length > 1) parts.push(`${view.latest.filter((row) => row.state === 'succeeded').length} de ${view.latest.length} itens`)
  if (view.loopCount > 0) parts.push(view.maxLoops ? `volta ${view.loopCount} de ${view.maxLoops}` : `volta ${view.loopCount}`)
  if (view.attempts > 1) parts.push(`${view.attempts} tentativas`)
  const single = view.latest.length === 1 ? view.latest[0] : null
  return (
    <li className="pipeline-run__step" data-testid={`run-step-${view.stepId}`} data-state={view.state}>
      <div className="pipeline-run__step-line">
        <span className={`pipeline-dot pipeline-dot--${toneOf(view.state)}`} aria-hidden="true" />
        <div className="pipeline-run__step-main">
          <span className="pipeline-run__step-name">{view.name}{view.name !== view.stepId ? <code>{view.stepId}</code> : null}</span>
          <span className={`pipeline-badge pipeline-badge--${tone}`}>{STEP_STATE_LABEL[view.state]}</span>
          {view.kind && <span className="pipeline-muted">{view.kind === 'gate' ? 'gate' : 'agente'}</span>}
          {parts.length > 0 && <span className="pipeline-muted" data-testid={`run-step-detail-${view.stepId}`}>{parts.join(' · ')}</span>}
        </div>
        <span className="pipeline-run__step-num">{tokens > 0 ? `${formatTokens(tokens)} tokens` : '—'}</span>
        <span className="pipeline-run__step-num">{view.rows.length ? formatCost(view.costUsd, view.costKnown) : '—'}</span>
        <span className="pipeline-run__step-out">
          {single?.hasOutput && <button type="button" className="pipeline-link-button" onClick={() => onOpenOutput(single)}>Ver saída</button>}
        </span>
      </div>
      {single?.error && <p className="pipeline-run__step-error">Erro: {single.error}</p>}
      {multi && (
        <details className="pipeline-run__items">
          <summary>{view.latest.length > 1 ? `Itens e tentativas (${view.rows.length})` : `Tentativas (${view.rows.length})`}</summary>
          <table className="pipeline-table">
            <caption className="pipeline-sr">Execuções da etapa {view.stepId}</caption>
            <thead><tr><th scope="col">Item</th><th scope="col">Volta</th><th scope="col">Tentativa</th><th scope="col">Estado</th><th scope="col">Agente</th><th scope="col">Tokens</th><th scope="col">Custo</th><th scope="col"><span className="pipeline-sr">Saída</span></th></tr></thead>
            <tbody>
              {view.rows.map((row) => (
                <tr key={`${row.itemKey}-${row.loopCount}-${row.attempt}`}>
                  <td>{itemLabel(row.itemKey)}</td>
                  <td>{row.loopCount}</td>
                  <td>{row.attempt}</td>
                  <td>{STEP_STATE_LABEL[row.state]}{row.error ? ` · ${row.error}` : ''}</td>
                  <td>{row.agentId ? <code>{row.agentId}</code> : '—'}</td>
                  <td>{formatTokens(row.inputTokens + row.outputTokens)}</td>
                  <td>{formatCost(row.costUsd, true)}</td>
                  <td>{row.hasOutput ? <button type="button" className="pipeline-link-button" onClick={() => onOpenOutput(row)}>Ver saída</button> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </li>
  )
}

function UsageTable({ title, rows, nameOf }: { title: string; rows: readonly UsageRow[]; nameOf?: (row: UsageRow) => string }) {
  if (rows.length === 0) return null
  return (
    <div className="pipeline-usage">
      <h4>{title}</h4>
      <table className="pipeline-table">
        <thead><tr><th scope="col">Nome</th><th scope="col">Execuções</th><th scope="col">Tokens</th><th scope="col">Custo</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td><code>{nameOf ? nameOf(row) : row.key}</code></td>
              <td>{row.attempts}</td>
              <td>{formatTokens(row.totalTokens)}</td>
              <td>{formatCost(row.costUsd, row.costKnown)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PipelineRunView({ client, runId }: { client: ApiClient; runId: string }) {
  const navigate = useNavigate()
  const { run, definition, budget, error, loading, refresh } = usePipelineRun(client, runId)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [openRow, setOpenRow] = useState<PipelineStepRow | null>(null)
  const [changed, setChanged] = useState<DefinitionChangedInfo | null>(null)
  const [choice, setChoice] = useState<'original' | 'new_run'>('original')
  const [rerunGates, setRerunGates] = useState(false)
  const [askBudget, setAskBudget] = useState(false)
  const [budgetAck, setBudgetAck] = useState(false)

  const steps = useMemo(
    () => (run ? buildStepViews(run, definition ? definition.steps : null, definition?.text ?? null) : []),
    [run, definition],
  )

  async function act(work: () => Promise<unknown>) {
    setBusy(true)
    setActionError(null)
    try {
      const result = await work()
      await refresh()
      return result
    } catch (caught: unknown) {
      const info = definitionChangedOf(caught)
      if (info) {
        setChanged(info)
        setChoice(info.options.some((option) => option.id === 'original') ? 'original' : 'new_run')
      } else if (caught instanceof ApiError && caught.code === 'PIPELINE_BUDGET_OVERRIDE_REQUIRED') {
        setAskBudget(true)
      } else if (caught instanceof ApiError && caught.serverMessage) {
        setActionError(caught.serverMessage)
      } else {
        setActionError('Não foi possível executar a ação. Tente de novo.')
      }
      return undefined
    } finally {
      setBusy(false)
    }
  }

  async function resume(options: { definition?: 'original' | 'new_run'; budgetOverride?: boolean } = {}) {
    if (!run) return
    const result = await act(() => resumePipelineRun(client, run.runId, { ...options, rerunGates }))
    if (result && typeof result === 'object') {
      setChanged(null)
      setAskBudget(false)
      setBudgetAck(false)
      const newRun = (result as { run_id?: unknown }).run_id
      if (options.definition === 'new_run' && typeof newRun === 'string' && newRun !== run.runId) navigate(`/pipelines/runs/${encodeURIComponent(newRun)}`)
    }
  }

  if (loading && !run) return <p className="pipeline-muted" role="status">Lendo a execução…</p>
  if (!run) return <p className="pipeline-note pipeline-note--bad" role="alert">{error ?? 'Execução não encontrada.'}</p>

  const stateText = RUN_STATE_LABEL[run.state] ?? run.state
  const pauseRequested = run.state === 'running' && run.stopReason === 'pause_requested'
  const stop = run.state === 'running' ? null : stopReasonText(run.stopReason)
  const costKnown = run.total ? run.total.costKnown : true
  const tokensOfCeiling = budget?.maxTokens ?? null
  const percent = tokensOfCeiling ? Math.min(100, Math.round((run.usedTokens / tokensOfCeiling) * 100)) : null

  return (
    <div className="pipeline-run" data-testid="pipeline-run-view" data-state={run.state}>
      <header className="pipeline-run__head">
        <div>
          <p className="eyebrow">PIPELINES / EXECUÇÃO</p>
          <h1><code>{run.pipelineId}</code></h1>
          <p className="pipeline-muted">
            Execução <code>{run.runId}</code>
            {run.conversationId && <> · <Link className="pipeline-link" to={`/chats/${encodeURIComponent(run.conversationId)}`}>abrir a conversa</Link></>}
            {' · '}<Link className="pipeline-link" to={`/pipelines?conversation=${encodeURIComponent(run.conversationId)}&pipeline=${encodeURIComponent(run.pipelineId)}`}>ver o pipeline</Link>
          </p>
        </div>
        <div className="pipeline-run__state">
          <span className={`pipeline-badge pipeline-badge--lg pipeline-badge--${toneOf(run.state)}`} data-testid="run-state">{stateText}</span>
          {pauseRequested && <span className="pipeline-muted" role="status">Pausa pedida: os agentes em andamento terminam e o pipeline para.</span>}
          {stop && <span className="pipeline-muted">Parou porque: {stop}.</span>}
        </div>
      </header>

      <section className="pipeline-run__totals" aria-label="Uso da execução" data-testid="run-totals">
        <dl className="pipeline-facts pipeline-facts--wide">
          <div><dt>Tokens</dt><dd>{formatTokens(run.usedTokens)}{tokensOfCeiling ? ` de ${formatTokens(tokensOfCeiling)}` : ''}</dd></div>
          <div><dt>Custo</dt><dd>{formatCost(run.usedCostUsd, costKnown)}{budget?.maxCostUsd ? ` de ${formatCost(budget.maxCostUsd)}` : ''}</dd></div>
          <div><dt>Tempo</dt><dd>{formatSeconds(run.usedSeconds)}{budget?.maxMinutes ? ` de ${budget.maxMinutes.toLocaleString('pt-BR')} min` : ''}</dd></div>
        </dl>
        {percent !== null && (
          <div className="pipeline-meter" role="img" aria-label={`${percent}% do teto de tokens usado`}>
            <span style={{ width: `${percent}%` }} data-high={percent >= 90 ? 'true' : undefined} />
          </div>
        )}
      </section>

      <div className="pipeline-run__actions" role="group" aria-label="Controles da execução">
        {run.state === 'running' && (
          <>
            <button type="button" className="button button--secondary pipelines-btn" disabled={busy || pauseRequested} onClick={() => void act(() => pausePipelineRun(client, run.runId))}>Pausar</button>
            <button type="button" className="button button--secondary pipelines-btn pipelines-btn--danger" disabled={busy} onClick={() => void act(() => cancelPipelineRun(client, run.runId))}>Cancelar</button>
          </>
        )}
        {run.state !== 'running' && run.state !== 'succeeded' && run.resumable && !run.needsBudgetOverride && (
          <>
            <button type="button" className="button button--primary pipelines-btn" disabled={busy} onClick={() => void resume()}>Retomar</button>
            <label className="pipeline-field__check">
              <input type="checkbox" checked={rerunGates} disabled={busy} onChange={(event) => setRerunGates(event.target.checked)} />
              Rodar os gates de novo ao retomar
            </label>
          </>
        )}
        {run.needsBudgetOverride && (
          <button type="button" className="button button--primary pipelines-btn" disabled={busy} onClick={() => setAskBudget(true)}>Continuar com novo orçamento</button>
        )}
      </div>
      {actionError && <p className="pipeline-note pipeline-note--bad" role="alert">{actionError}</p>}
      {error && <p className="pipeline-note pipeline-note--warn" role="status">{error}</p>}

      {run.state === 'running' && (
        <p className="pipeline-note" data-testid="run-busy-note">
          A conversa fica ocupada enquanto o pipeline roda: o que você escrever nela entra na fila e só é lido quando ele terminar.
        </p>
      )}

      {askBudget && (
        <section className="pipeline-confirm" role="group" aria-label="Continuar depois que o orçamento acabou" data-testid="budget-override">
          <h3>O orçamento acabou</h3>
          <p>
            Continuar mesmo assim zera o contador de gasto desta execução: o teto{tokensOfCeiling ? ` de ${formatTokens(tokensOfCeiling)} tokens` : ''} vale de novo a partir de agora, além do que já foi gasto
            ({formatTokens(run.usedTokens)} tokens, {formatCost(run.usedCostUsd, costKnown)}).
          </p>
          <label className="pipeline-approval__ack">
            <input type="checkbox" checked={budgetAck} onChange={(event) => setBudgetAck(event.target.checked)} />
            Entendo que isso gasta mais assinatura
          </label>
          <div className="pipeline-form__actions">
            <button type="button" className="button button--primary pipelines-btn" disabled={busy || !budgetAck} onClick={() => void resume({ budgetOverride: true })}>Continuar com novo teto</button>
            <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={() => { setAskBudget(false); setBudgetAck(false) }}>Voltar</button>
          </div>
        </section>
      )}

      {changed && (
        <section className="pipeline-confirm" role="group" aria-label="A definição do pipeline mudou" data-testid="definition-changed">
          <h3>A definição do pipeline mudou</h3>
          <p>O arquivo foi alterado depois que esta execução começou. As etapas já feitas valem para a definição antiga: escolha como continuar.</p>
          <div className="pipeline-field__choices" role="radiogroup" aria-label="Como continuar">
            {changed.options.map((option) => (
              <label key={option.id}>
                <input type="radio" name="definition-choice" checked={choice === option.id} onChange={() => setChoice(option.id)} />
                {option.label || (option.id === 'original' ? 'Retomar com a definição original' : 'Iniciar uma nova execução com a definição atual')}
              </label>
            ))}
          </div>
          {choice === 'original' && <p className="pipeline-muted">Se você mexeu nos arquivos do projeto à mão, marque &quot;Rodar os gates de novo&quot;: um gate antigo não vale para o código novo.</p>}
          <div className="pipeline-form__actions">
            <button type="button" className="button button--primary pipelines-btn" disabled={busy} onClick={() => void resume({ definition: choice })}>Continuar</button>
            <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={() => setChanged(null)}>Voltar</button>
          </div>
        </section>
      )}

      {budget?.isDefault && run.state === 'running' && <p className="pipeline-muted">Este pipeline não define orçamento: vale o teto padrão do Orin.</p>}

      <section className="pipeline-run__steps" aria-label="Etapas">
        <div className="pipeline-run__steps-head" aria-hidden="true">
          <span>Etapa</span><span>Tokens</span><span>Custo</span><span />
        </div>
        <ol className="pipeline-run__list">
          {steps.map((view) => (
            <StepRowView key={view.stepId} view={view} onOpenOutput={setOpenRow} />
          ))}
        </ol>
        {steps.length === 0 && <p className="pipeline-muted">Nenhuma etapa registrada ainda.</p>}
      </section>

      {openRow && <OutputPanel key={`${openRow.stepId}-${openRow.itemKey}-${openRow.attempt}`} client={client} runId={run.runId} row={openRow} onClose={() => setOpenRow(null)} />}

      <section className="pipeline-run__usage" aria-label="Uso por agente e por assinatura">
        <UsageTable title="Por assinatura" rows={run.byProvider} />
        <UsageTable title="Por agente" rows={run.byAgent} />
        {!run.consistent && <p className="pipeline-note pipeline-note--warn">O total da execução e a soma das etapas divergem; o diário é a referência.</p>}
      </section>
    </div>
  )
}

export function PipelineRunPage({ client }: { client?: ApiClient }) {
  const { runId = '' } = useParams()
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  return (
    <div className="pipelines-page" data-testid="pipeline-run-page">
      <PipelineRunView key={runId} client={apiClient} runId={runId} />
    </div>
  )
}
