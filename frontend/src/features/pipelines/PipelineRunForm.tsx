import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import {
  approvePipeline,
  estimatePipeline,
  reviewOf,
  startPipelineRun,
  type PipelineDetail,
  type PipelineEstimate,
  type PipelineInputSpec,
  type PipelineReview,
  type StartedRun,
} from '../../api/pipelines'
import { formatCost, formatTokens } from './format'
import './pipelines.css'

type ModelsMode = 'auto' | 'manual'
type FieldValue = { text: string; flag: boolean; mode: ModelsMode }

const ESTIMATE_DELAY_MS = 400

const START_ERRORS: Record<string, string> = {
  E_DISABLED: 'Os pipelines estão desligados em Configurações > Orquestração.',
  PIPELINE_NOT_FOUND: 'Este pipeline não existe mais. Atualize a lista.',
  PIPELINE_NOT_APPROVED: 'Este pipeline do projeto ainda não foi aprovado. Revise os comandos abaixo.',
  PIPELINE_PLAN_MODE: 'A conversa está no Modo Plano, que não roda pipelines. Saia do Modo Plano e tente de novo.',
  PIPELINE_INVALID: 'O pipeline tem erros e não pode rodar. Corrija o arquivo.',
  PIPELINE_MODELS_UNAVAILABLE: 'Nenhum modelo favorito está disponível para a entrada de modelos. Marque favoritos ou informe provedor/modelo.',
  PIPELINE_INPUT_REQUIRED: 'Falta preencher uma entrada obrigatória.',
  PIPELINE_INPUT_INVALID: 'Uma entrada tem um valor inválido.',
  PIPELINE_INPUT_UNKNOWN: 'O pipeline não tem uma das entradas enviadas.',
}

function describeStartError(error: unknown): string {
  if (error instanceof ApiError) {
    if (START_ERRORS[error.code]) return error.serverMessage && error.code.startsWith('PIPELINE_INPUT') ? error.serverMessage : START_ERRORS[error.code]
    if (error.status === 429) return 'Muitos pedidos seguidos. Espere um instante e tente de novo.'
    if (error.serverMessage) return error.serverMessage
  }
  return 'Não foi possível iniciar o pipeline. Tente de novo.'
}

function initialField(spec: PipelineInputSpec, preset: string | undefined): FieldValue {
  const fallback = spec.defaultValue
  if (spec.type === 'boolean') {
    const raw = preset ?? (typeof fallback === 'boolean' ? String(fallback) : 'false')
    return { text: '', flag: raw === 'true', mode: 'auto' }
  }
  if (spec.type === 'models') {
    const raw = preset ?? (Array.isArray(fallback) ? fallback.map(String).join('\n') : typeof fallback === 'string' ? fallback : 'auto')
    return raw.trim().toLowerCase() === 'auto' || raw.trim() === '' ? { text: '', flag: false, mode: 'auto' } : { text: raw.split(/[\n,]+/).map((part) => part.trim()).filter(Boolean).join('\n'), flag: false, mode: 'manual' }
  }
  return { text: preset ?? (fallback === null || fallback === undefined ? '' : String(fallback)), flag: false, mode: 'auto' }
}

function typedInputs(specs: readonly PipelineInputSpec[], values: Record<string, FieldValue>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const spec of specs) {
    const value = values[spec.name]
    if (!value) continue
    if (spec.type === 'boolean') out[spec.name] = value.flag
    else if (spec.type === 'models') out[spec.name] = value.mode === 'auto' ? 'auto' : value.text.split(/[\n,]+/).map((part) => part.trim()).filter(Boolean)
    else if (spec.type === 'integer') {
      if (value.text.trim() !== '' && /^-?\d+$/.test(value.text.trim())) out[spec.name] = Number(value.text.trim())
    } else if (value.text.trim() !== '') out[spec.name] = value.text
  }
  return out
}

function missingRequired(specs: readonly PipelineInputSpec[], values: Record<string, FieldValue>): string[] {
  return specs
    .filter((spec) => spec.required && spec.defaultValue === null)
    .filter((spec) => {
      const value = values[spec.name]
      if (!value) return true
      if (spec.type === 'boolean') return false
      if (spec.type === 'models') return value.mode === 'manual' && value.text.trim() === ''
      return value.text.trim() === ''
    })
    .map((spec) => spec.name)
}

function ModelsField({ spec, value, onChange, disabled, resolved }: {
  spec: PipelineInputSpec
  value: FieldValue
  onChange: (next: FieldValue) => void
  disabled: boolean
  resolved: Array<{ provider: string; model: string }> | null
}) {
  const id = useId()
  return (
    <div className="pipeline-field__models">
      <div className="pipeline-field__choices" role="radiogroup" aria-label={`Modelos de ${spec.name}`}>
        <label>
          <input type="radio" name={`${id}-mode`} checked={value.mode === 'auto'} disabled={disabled} onChange={() => onChange({ ...value, mode: 'auto' })} />
          Automático (modelos favoritos de famílias diferentes)
        </label>
        <label>
          <input type="radio" name={`${id}-mode`} checked={value.mode === 'manual'} disabled={disabled} onChange={() => onChange({ ...value, mode: 'manual' })} />
          Escolher
        </label>
      </div>
      {value.mode === 'manual' && (
        <textarea
          aria-label={`Lista de provedor/modelo para ${spec.name}`}
          rows={3}
          placeholder={'claude/claude-sonnet-5-5\ncodex/gpt-6-luna'}
          value={value.text}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, text: event.target.value })}
        />
      )}
      {resolved && resolved.length > 0 && (
        <ul className="pipeline-field__resolved" data-testid={`resolved-${spec.name}`}>
          {resolved.map((ref) => <li key={`${ref.provider}/${ref.model}`}><code>{ref.provider}/{ref.model}</code></li>)}
        </ul>
      )}
    </div>
  )
}

function resolvedModels(estimate: PipelineEstimate | null, name: string): Array<{ provider: string; model: string }> | null {
  const raw = estimate?.inputs[name]
  if (!Array.isArray(raw)) return null
  const refs: Array<{ provider: string; model: string }> = []
  for (const item of raw) {
    if (item && typeof item === 'object' && 'provider' in item && 'model' in item) {
      refs.push({ provider: String((item as { provider: unknown }).provider), model: String((item as { model: unknown }).model) })
    }
  }
  return refs
}

export function ReviewBlock({ review }: { review: PipelineReview }) {
  return (
    <div className="pipeline-review" data-testid="gate-review">
      {review.notice && <p className="pipeline-review__notice">{review.notice}</p>}
      {review.commands.length === 0 ? (
        <p className="pipeline-muted">Este pipeline não roda comandos de gate.</p>
      ) : (
        <ol className="pipeline-review__list">
          {review.commands.map((command, index) => (
            <li key={`${command.stepId}-${index}`}>
              <span className="pipeline-review__step">Etapa <code>{command.stepId}</code></span>
              <code className="pipeline-review__command">{command.command}</code>
              <span className="pipeline-muted">
                {command.timeoutSeconds ? `limite de ${command.timeoutSeconds} s` : 'sem limite próprio'}
                {command.expect ? ` · espera: ${command.expect}` : ''}
                {command.wholeValue ? ' · o comando inteiro vem de uma entrada' : ''}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export function PipelineRunForm({
  client,
  pipeline,
  conversationId,
  presets = {},
  onStarted,
  onApproved,
  onCancel,
}: {
  client: ApiClient
  pipeline: PipelineDetail
  conversationId: string | null
  /** Values typed in `/pipeline <id> chave=valor`, by input name. */
  presets?: Record<string, string>
  onStarted: (run: StartedRun) => void
  onApproved?: () => void
  onCancel?: () => void
}) {
  const [values, setValues] = useState<Record<string, FieldValue>>(() =>
    Object.fromEntries(pipeline.inputs.map((spec) => [spec.name, initialField(spec, presets[spec.name])])),
  )
  const [estimate, setEstimate] = useState<PipelineEstimate | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  // The inputs the last estimate answer (or failure) was for: other inputs mean an estimate is on its way.
  const [estimatedFor, setEstimatedFor] = useState<string | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refusedReview, setRefusedReview] = useState<PipelineReview | null>(null)
  const sequence = useRef(0)

  const payload = useMemo(() => typedInputs(pipeline.inputs, values), [pipeline.inputs, values])
  const payloadKey = JSON.stringify(payload)
  const missing = useMemo(() => missingRequired(pipeline.inputs, values), [pipeline.inputs, values])
  const estimating = Boolean(conversationId) && estimatedFor !== `${conversationId}|${pipeline.id}|${payloadKey}`

  useEffect(() => {
    if (!conversationId) return undefined
    const mine = ++sequence.current
    const controller = new AbortController()
    const key = `${conversationId}|${pipeline.id}|${payloadKey}`
    const timer = window.setTimeout(() => {
      estimatePipeline(client, pipeline.id, JSON.parse(payloadKey) as Record<string, unknown>, conversationId, controller.signal)
        .then((next) => {
          if (sequence.current !== mine) return
          setEstimate(next)
          setEstimateError(null)
        })
        .catch((caught: unknown) => {
          if (sequence.current !== mine || controller.signal.aborted) return
          setEstimate(null)
          setEstimateError(caught instanceof ApiError && caught.serverMessage ? caught.serverMessage : 'Não foi possível listar as assinaturas envolvidas agora.')
        })
        .finally(() => { if (sequence.current === mine) setEstimatedFor(key) })
    }, ESTIMATE_DELAY_MS)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [client, conversationId, pipeline.id, payloadKey])

  const review = refusedReview ?? pipeline.review
  const needsApproval = pipeline.approvalRequired && (!pipeline.approved || refusedReview !== null)
  const planMode = estimate?.permissionMode === 'plan_mode' || estimate?.permissionMode === 'plan'
  const askMode = estimate !== null && ['', 'ask_permissions', 'default'].includes(estimate.permissionMode)
  const blockedProviders = estimate?.quota.blocked ?? []
  const canRun = Boolean(conversationId) && missing.length === 0 && !planMode && !busy && (!needsApproval || acknowledged)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!canRun || !conversationId) return
    setBusy(true)
    setError(null)
    try {
      if (needsApproval) {
        await approvePipeline(client, pipeline.id, pipeline.definitionHash, conversationId)
        setRefusedReview(null)
        onApproved?.()
      }
      onStarted(await startPipelineRun(client, pipeline.id, conversationId, payload))
    } catch (caught: unknown) {
      const refused = reviewOf(caught)
      if (refused) {
        setRefusedReview(refused)
        setAcknowledged(false)
      }
      setError(describeStartError(caught))
    } finally {
      setBusy(false)
    }
  }

  function setField(name: string, next: FieldValue) {
    setValues((current) => ({ ...current, [name]: next }))
  }

  return (
    <form className="pipeline-form" onSubmit={submit} aria-label={`Rodar ${pipeline.name}`} data-testid="pipeline-run-form">
      {!conversationId && (
        <p className="pipeline-note pipeline-note--warn" role="note">
          Escolha uma conversa para rodar o pipeline: ele usa a pasta, o modo de permissão e a assinatura dela.
        </p>
      )}

      <fieldset className="pipeline-form__inputs">
        <legend>Entradas</legend>
        {pipeline.inputs.length === 0 && <p className="pipeline-muted">Este pipeline não tem entradas.</p>}
        {pipeline.inputs.map((spec) => {
          const value = values[spec.name] ?? initialField(spec, undefined)
          const fieldId = `pipeline-input-${spec.name}`
          const long = spec.type === 'string' && (spec.maxLength ?? 0) >= 1000
          return (
            <div className="pipeline-field" key={spec.name}>
              <label htmlFor={spec.type === 'models' ? undefined : fieldId} className="pipeline-field__label">
                <code>{spec.name}</code>
                {spec.required ? <span className="pipeline-field__req"> obrigatória</span> : <span className="pipeline-muted"> opcional</span>}
              </label>
              {spec.type === 'boolean' ? (
                <label className="pipeline-field__check">
                  <input id={fieldId} type="checkbox" checked={value.flag} disabled={busy} onChange={(event) => setField(spec.name, { ...value, flag: event.target.checked })} />
                  {value.flag ? 'Ligado' : 'Desligado'}
                </label>
              ) : spec.type === 'models' ? (
                <ModelsField spec={spec} value={value} disabled={busy} onChange={(next) => setField(spec.name, next)} resolved={resolvedModels(estimate, spec.name)} />
              ) : spec.type === 'integer' ? (
                <input id={fieldId} type="number" inputMode="numeric" step={1} value={value.text} disabled={busy} onChange={(event) => setField(spec.name, { ...value, text: event.target.value })} />
              ) : long ? (
                <textarea id={fieldId} rows={4} maxLength={spec.maxLength ?? undefined} value={value.text} disabled={busy} onChange={(event) => setField(spec.name, { ...value, text: event.target.value })} />
              ) : (
                <input id={fieldId} type="text" maxLength={spec.maxLength ?? undefined} value={value.text} disabled={busy} onChange={(event) => setField(spec.name, { ...value, text: event.target.value })} />
              )}
              {spec.wholeCommand && (
                <p className="pipeline-field__hint">Esta entrada é o comando inteiro de um gate: ele roda na sua máquina, sob as suas permissões. O agente nunca a preenche.</p>
              )}
            </div>
          )
        })}
      </fieldset>

      <section className="pipeline-before" aria-label="Antes de rodar" data-testid="pipeline-before">
        <h3>Antes de rodar</h3>
        <p className="pipeline-muted">Isto lista os modelos e as assinaturas envolvidos. Não é uma estimativa de tokens.</p>
        {estimating && !estimate && <p className="pipeline-muted" role="status">Calculando as assinaturas envolvidas…</p>}
        {estimateError && <p className="pipeline-note pipeline-note--warn" role="status">{estimateError}</p>}
        {estimate && (
          <>
            <div className="pipeline-before__grid">
              <div>
                <h4>Limites do pipeline</h4>
                <dl className="pipeline-facts" data-testid="estimate-budget">
                  <div><dt>Tokens</dt><dd>{estimate.budget.maxTokens === null ? 'sem teto' : formatTokens(estimate.budget.maxTokens)}</dd></div>
                  <div><dt>Custo</dt><dd>{estimate.budget.maxCostUsd === null ? 'sem teto' : formatCost(estimate.budget.maxCostUsd)}</dd></div>
                  <div><dt>Tempo</dt><dd>{estimate.budget.maxMinutes === null ? 'sem teto' : `${estimate.budget.maxMinutes.toLocaleString('pt-BR')} min`}</dd></div>
                  <div data-testid="estimate-parallel"><dt>Paralelismo máximo</dt><dd>{estimate.maxParallel} {estimate.maxParallel === 1 ? 'agente' : 'agentes ao mesmo tempo'}</dd></div>
                </dl>
                {estimate.budget.isDefault && <p className="pipeline-muted">O pipeline não define orçamento: vale o teto padrão do Orin.</p>}
                {estimate.budget.loweredByUserCeiling && <p className="pipeline-muted">O orçamento foi reduzido pelo seu teto em Configurações.</p>}
              </div>
              <div>
                <h4>Assinaturas envolvidas</h4>
                {estimate.subscriptions.length === 0 ? (
                  <p className="pipeline-muted">Os modelos de cada etapa são escolhidos ao rodar (a conversa define o padrão).</p>
                ) : (
                  <ul className="pipeline-subs" data-testid="estimate-subscriptions">
                    {estimate.subscriptions.map((sub) => {
                      const blocked = blockedProviders.includes(sub.provider)
                      const warn = estimate.quota.warn.includes(sub.provider)
                      return (
                        <li key={`${sub.provider}/${sub.model}`}>
                          <code>{sub.provider}{sub.model ? ` · ${sub.model}` : ''}</code>
                          <span className="pipeline-muted">{sub.steps.join(', ')}</span>
                          {blocked && <span className="pipeline-badge pipeline-badge--bad">Cota bloqueada</span>}
                          {!blocked && warn && <span className="pipeline-badge pipeline-badge--warn">Cota alta</span>}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            </div>

            <table className="pipeline-table" data-testid="estimate-steps">
              <caption className="pipeline-sr">Etapas, agentes e modelos</caption>
              <thead><tr><th scope="col">Etapa</th><th scope="col">Quem faz</th><th scope="col">Modelo</th><th scope="col">Esforço</th></tr></thead>
              <tbody>
                {estimate.steps.map((step) => (
                  <tr key={step.id}>
                    <td><code>{step.id}</code>{step.map ? <span className="pipeline-muted"> · até {step.maxParallel} ao mesmo tempo</span> : null}</td>
                    <td>{step.kind === 'gate' ? 'Gate (comando e verificação)' : step.agent ?? 'agente'}{step.isolation === 'worktree' ? ' · worktree' : ''}</td>
                    <td>{step.kind === 'gate' ? '—' : [step.provider, step.model].filter(Boolean).join(' · ') || 'o da conversa'}</td>
                    <td>{step.effort ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <ul className="pipeline-notes" aria-label="Avisos">
              {blockedProviders.length > 0 && (
                <li className="pipeline-note pipeline-note--bad" role="alert">
                  A cota de {blockedProviders.join(', ')} está acima do limite de segundo plano: as etapas dessa assinatura vão falhar até a cota voltar.
                </li>
              )}
              {estimate.quota.warn.length > 0 && blockedProviders.length === 0 && (
                <li className="pipeline-note pipeline-note--warn">A cota de {estimate.quota.warn.join(', ')} está alta. Um pipeline em paralelo gasta rápido.</li>
              )}
              {estimate.degraded.map((note) => (
                <li key={`${note.reason}-${note.detail}`} className="pipeline-note pipeline-note--warn" data-testid="estimate-degraded">
                  {note.detail || note.reason}{note.effect ? ` — ${note.effect}` : ''}
                </li>
              ))}
              {planMode && <li className="pipeline-note pipeline-note--bad" role="alert">A conversa está no Modo Plano, que recusa pipelines. Saia dele para rodar.</li>}
              {askMode && <li className="pipeline-note pipeline-note--warn" data-testid="estimate-permission">O modo de permissão pede aprovação a cada ação: o pipeline pode parar esperando você. Considere Aceitar edições.</li>}
              {estimate.notices.filter((notice) => !/estimativa de tokens/i.test(notice) && !/aprovações durante/i.test(notice)).map((notice) => (
                <li key={notice} className="pipeline-note">{notice}</li>
              ))}
              <li className="pipeline-note" data-testid="estimate-conversation-busy">
                A conversa fica ocupada enquanto o pipeline roda. O que você escrever nela entra na fila e só é lido quando ele terminar; use Pausar ou Cancelar para interromper.
              </li>
            </ul>
          </>
        )}
      </section>

      {needsApproval && review && (
        <section className="pipeline-approval" aria-label="Aprovação do pipeline do projeto" data-testid="pipeline-approval">
          <h3>Este pipeline é do projeto e roda comandos</h3>
          <p className="pipeline-muted">Revise os comandos abaixo. A aprovação vale para este arquivo exato: se ele mudar, será preciso aprovar de novo.</p>
          <ReviewBlock review={review} />
          <label className="pipeline-approval__ack">
            <input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} />
            Revisei os comandos e aprovo rodar este pipeline neste projeto
          </label>
        </section>
      )}

      {missing.length > 0 && <p className="pipeline-muted" role="status">Preencha: {missing.join(', ')}.</p>}
      {error && <p className="pipeline-note pipeline-note--bad" role="alert">{error}</p>}

      <div className="pipeline-form__actions">
        <button type="submit" className="button button--primary pipelines-btn" disabled={!canRun}>
          {busy ? 'Iniciando…' : needsApproval ? 'Aprovar e rodar' : 'Rodar'}
        </button>
        {onCancel && <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={onCancel}>Voltar</button>}
      </div>
    </form>
  )
}
