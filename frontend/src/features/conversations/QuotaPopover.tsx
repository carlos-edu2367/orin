import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import {
  emptyProviderQuota,
  ensureAllSubscriptions,
  formatQuotaAge,
  formatResetTime,
  highestKnownPercent,
  isQuotaRejected,
  normalizeSubscriptionProvider,
  quotaConfidenceLabel,
  quotaSourceLabel,
  quotaStatusLabel,
  quotaWindowLabel,
  subscriptionDisplayName,
  type ProviderQuota,
} from '../../api/quota'
import { QuotaRing, quotaToneFor } from '../../components/QuotaRing'
import './QuotaPopover.css'

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

            <div className="quota-card__windows">
              {sub.windows.length > 0 ? (
                sub.windows.map((win, index) => {
                  const winTone = quotaToneFor(win.used_percent, win.status === 'rejected')
                  const reset = formatResetTime(win.resets_at, nowMs)
                  return (
                    <div key={`${win.kind}-${win.scope_model ?? index}`} className="quota-card__window">
                      <div className="quota-card__window-row">
                        <span className="quota-card__window-label">{quotaWindowLabel(win)}</span>
                        <span className="quota-card__window-val">
                          {win.used_percent !== null
                            ? `${Math.round(win.used_percent)}%`
                            : quotaStatusLabel(win.status)}
                        </span>
                      </div>
                      {win.used_percent !== null && (
                        <div className="quota-card__bar" aria-hidden="true">
                          <span
                            className={`quota-card__bar-fill quota-card__bar-fill--${winTone}`}
                            style={{ width: `${Math.max(0, Math.min(100, win.used_percent))}%` }}
                          />
                        </div>
                      )}
                      {reset && (
                        <div className="quota-card__reset">Libera às {reset}</div>
                      )}
                    </div>
                  )
                })
              ) : (
                <p className="quota-card__empty">
                  {sub.provider === 'google-antigravity'
                    ? 'O Antigravity não informa a cota.'
                    : 'Sem janela registrada ainda.'}
                </p>
              )}
            </div>

            <dl className="quota-card__meta">
              <div>
                <dt>Fonte</dt>
                <dd>{quotaSourceLabel(sub.source)}</dd>
              </div>
              <div>
                <dt>Idade</dt>
                <dd>{age}</dd>
              </div>
            </dl>

            <div className="quota-card__orin-usage">
              <span className="quota-card__usage-heading">Consumo pelo Orin</span>
              <dl className="quota-card__usage-grid">
                <div>
                  <dt>5 h</dt>
                  <dd>
                    {sub.orin_usage.five_hour.tokens.toLocaleString('pt-BR')} tokens ·{' '}
                    {sub.orin_usage.five_hour.calls}{' '}
                    {sub.orin_usage.five_hour.calls === 1 ? 'chamada' : 'chamadas'}
                  </dd>
                </div>
                <div>
                  <dt>7 dias</dt>
                  <dd>
                    {sub.orin_usage.seven_day.tokens.toLocaleString('pt-BR')} tokens ·{' '}
                    {sub.orin_usage.seven_day.calls}{' '}
                    {sub.orin_usage.seven_day.calls === 1 ? 'chamada' : 'chamadas'}
                  </dd>
                </div>
              </dl>
            </div>
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
  const containerRef = useRef<HTMLDivElement>(null)
  const normalizedProvider = normalizeSubscriptionProvider(provider)

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
        nowMs={nowMs}
        onClick={() => setOpen((current) => !current)}
      />

      {open && (
        <div
          className="picker-panel quota-popover__panel"
          role="dialog"
          aria-label="Cota das assinaturas"
          data-testid="quota-popover-panel"
        >
          <div className="quota-popover__header">
            <div>
              <h3 className="quota-popover__title">Cota das assinaturas</h3>
              <p className="quota-popover__subtitle">
                Leitura passiva das assinaturas locais e uso registrado pelo Orin.
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
            nowMs={nowMs}
            layout="popover"
          />
        </div>
      )}
    </div>
  )
}
