import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import {
  emptyProviderQuota,
  ensureAllSubscriptions,
  formatQuotaAge,
  formatQuotaRelease,
  highestKnownPercent,
  isQuotaRejected,
  isQuotaWindowRenewed,
  normalizeSubscriptionProvider,
  quotaConfidenceLabel,
  quotaSourceLabel,
  quotaStatusLabel,
  quotaWindowLabel,
  sortQuotaWindows,
  subscriptionDisplayName,
  type ProviderQuota,
  type QuotaWindow,
} from '../../api/quota'
import { QuotaRing, quotaToneFor } from '../../components/QuotaRing'
import { useQuotaNow } from './useQuotaNow'
import './QuotaPopover.css'

function windowValueText(win: QuotaWindow): string {
  return win.used_percent !== null ? `${Math.round(win.used_percent)}%` : quotaStatusLabel(win.status)
}

function windowNoteText(win: QuotaWindow, nowMs?: number): string | null {
  if (isQuotaWindowRenewed(win)) return 'janela renovada, aguardando leitura'
  return formatQuotaRelease(win.resets_at, nowMs)
}

function emptyMessage(provider: string): string {
  if (provider === 'google-antigravity') {
    return 'Sem leitura do Antigravity ainda. Use Atualizar para consultar o CLI.'
  }
  return 'Sem janela registrada ainda.'
}

function usageLine(window: { tokens: number; calls: number }): string {
  return `${window.tokens.toLocaleString('pt-BR')} tokens · ${window.calls} ${
    window.calls === 1 ? 'chamada' : 'chamadas'
  }`
}

export type SubscriptionQuotaCardsProps = {
  subscriptions: readonly ProviderQuota[]
  activeProvider?: string | null
  nowMs?: number
  layout?: 'popover' | 'settings'
}

export function SubscriptionQuotaCards({
  subscriptions,
  activeProvider,
  nowMs,
  layout = 'popover',
}: SubscriptionQuotaCardsProps) {
  const ordered = useMemo(() => ensureAllSubscriptions(subscriptions), [subscriptions])
  const normalizedActive = normalizeSubscriptionProvider(activeProvider)

  return (
    <div
      className={`quota-cards quota-cards--${layout}`}
      data-testid="quota-subscriptions-grid"
    >
      {ordered.map((sub) => {
        const percent = highestKnownPercent(sub)
        const rejected = isQuotaRejected(sub)
        const tone = quotaToneFor(percent, rejected)
        const age = formatQuotaAge(sub.observed_at, sub.age_seconds, nowMs)
        const isCurrent = normalizedActive === sub.provider
        const windows = sortQuotaWindows(sub.windows)

        return (
          <section
            key={sub.provider}
            className={`quota-card quota-card--${tone}${isCurrent ? ' is-current' : ''}`}
            data-testid={`quota-card-${sub.provider}`}
            aria-label={subscriptionDisplayName(sub.provider)}
          >
            <header className="quota-card__head">
              <div className="quota-card__title-group">
                <h4 className="quota-card__title">{subscriptionDisplayName(sub.provider)}</h4>
                {isCurrent && <span className="quota-card__active">Atual</span>}
              </div>
              <span className={`quota-card__badge quota-card__badge--${tone}`}>
                {rejected ? 'Esgotada' : quotaConfidenceLabel(sub.confidence)}
              </span>
            </header>

            {windows.length > 0 ? (
              <ul className="quota-card__windows">
                {windows.map((win, index) => {
                  const winTone = quotaToneFor(win.used_percent, win.status === 'rejected')
                  const note = windowNoteText(win, nowMs)
                  const label = quotaWindowLabel(win)
                  const models = win.scope_models && win.scope_models.length > 0 ? win.scope_models.join(', ') : null
                  return (
                    <li
                      key={`${win.kind}-${win.scope_model ?? index}`}
                      className="quota-card__window"
                      data-testid={`quota-window-${sub.provider}-${index}`}
                    >
                      <div className="quota-card__window-row">
                        <span
                          className="quota-card__window-label"
                          title={models ? `${label}\nModelos: ${models}` : label}
                        >
                          {label}
                        </span>
                        <span className="quota-card__window-val">{windowValueText(win)}</span>
                      </div>
                      <div className="quota-card__window-line">
                        <div className="quota-card__bar" aria-hidden="true">
                          {win.used_percent !== null && (
                            <span
                              className={`quota-card__bar-fill quota-card__bar-fill--${winTone}`}
                              style={{ width: `${Math.max(0, Math.min(100, win.used_percent))}%` }}
                            />
                          )}
                        </div>
                        {note && <span className="quota-card__reset">{note}</span>}
                      </div>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="quota-card__empty">{emptyMessage(sub.provider)}</p>
            )}

            <p className="quota-card__meta">
              <span>{quotaSourceLabel(sub.source)}</span>
              <span aria-hidden="true"> · </span>
              <span>{age}</span>
            </p>

            <details className="quota-card__orin-usage">
              <summary className="quota-card__usage-heading">Consumo pelo Orin</summary>
              <dl className="quota-card__usage-grid">
                <div>
                  <dt>5 h</dt>
                  <dd>{usageLine(sub.orin_usage.five_hour)}</dd>
                </div>
                <div>
                  <dt>7 dias</dt>
                  <dd>{usageLine(sub.orin_usage.seven_day)}</dd>
                </div>
              </dl>
            </details>
          </section>
        )
      })}
    </div>
  )
}

export type QuotaPopoverProps = {
  provider: string | null
  subscriptions: readonly ProviderQuota[]
  displayEnabled?: boolean
  loading?: boolean
  disabled?: boolean
  compact?: boolean
  /** Called when the panel opens and when "Atualizar" is clicked (asks the CLIs for the real usage). */
  onRefresh?: () => void
  nowMs?: number
}

export function QuotaPopover({
  provider,
  subscriptions,
  displayEnabled = true,
  loading = false,
  disabled = false,
  compact = false,
  onRefresh,
  nowMs,
}: QuotaPopoverProps) {
  const [open, setOpen] = useState(false)
  const { nowMs: effectiveNowMs, bump: bumpNow } = useQuotaNow(nowMs, displayEnabled)
  const containerRef = useRef<HTMLDivElement>(null)
  const onRefreshRef = useRef(onRefresh)
  const normalizedProvider = normalizeSubscriptionProvider(provider)

  useEffect(() => {
    onRefreshRef.current = onRefresh
  }, [onRefresh])

  // Opening the panel asks for fresh data (the server probes the CLIs; the result arrives through SSE).
  useEffect(() => {
    if (open) onRefreshRef.current?.()
  }, [open])

  const currentQuota = useMemo<ProviderQuota | null>(() => {
    if (!normalizedProvider) return null
    const found = subscriptions.find(
      (entry) => normalizeSubscriptionProvider(entry.provider) === normalizedProvider,
    )
    return found ?? emptyProviderQuota(normalizedProvider)
  }, [normalizedProvider, subscriptions])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent | MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!displayEnabled || !normalizedProvider) {
    return null
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    }
  }

  return (
    <div
      className="quota-popover"
      ref={containerRef}
      onKeyDown={handleKeyDown}
      data-testid="quota-popover"
    >
      <QuotaRing
        quota={currentQuota}
        provider={normalizedProvider}
        enabled={displayEnabled}
        expanded={open}
        disabled={disabled}
        compact={compact}
        nowMs={effectiveNowMs}
        onClick={() => {
          bumpNow()
          setOpen((current) => !current)
        }}
      />

      {open && (
        <div
          className="quota-popover__panel"
          role="dialog"
          aria-label="Cota das assinaturas"
          data-testid="quota-popover-panel"
        >
          <div className="quota-popover__header">
            <div className="quota-popover__heading">
              <h3 className="quota-popover__title">Cota das assinaturas</h3>
              <p className="quota-popover__subtitle">
                O Orin consulta os CLIs ao abrir, a cada turno e em intervalos regulares.
              </p>
            </div>
            <div className="quota-popover__actions">
              {onRefresh && (
                <button
                  type="button"
                  className="button button--quiet quota-popover__refresh"
                  disabled={loading}
                  onClick={onRefresh}
                >
                  {loading ? 'Atualizando…' : 'Atualizar'}
                </button>
              )}
              <button
                type="button"
                className="quota-popover__close"
                aria-label="Fechar painel de cota"
                onClick={() => setOpen(false)}
              >
                ×
              </button>
            </div>
          </div>

          <SubscriptionQuotaCards
            subscriptions={subscriptions}
            activeProvider={normalizedProvider}
            nowMs={effectiveNowMs}
            layout="popover"
          />
        </div>
      )}
    </div>
  )
}
