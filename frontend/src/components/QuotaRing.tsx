import {
  formatQuotaAge,
  formatQuotaRelease,
  sortQuotaWindows,
  highestKnownPercent,
  isQuotaRejected,
  normalizeSubscriptionProvider,
  quotaStatusLabel,
  quotaWindowLabel,
  subscriptionDisplayName,
  type ProviderQuota,
} from '../api/quota'
import './QuotaRing.css'

export type QuotaRingTone = 'ok' | 'warn' | 'danger' | 'unknown'

export type QuotaRingProps = {
  quota: ProviderQuota | null
  provider?: string | null
  /** Respects `quota.display.enabled`; renders nothing when false. */
  enabled?: boolean
  onClick?: () => void
  expanded?: boolean
  disabled?: boolean
  compact?: boolean
  /** Injectable reference timestamp in ms for deterministic rendering/tests. */
  nowMs?: number
}

const RING_RADIUS = 6
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS

export function quotaToneFor(percent: number | null, rejected: boolean): QuotaRingTone {
  if (rejected) return 'danger'
  if (percent === null) return 'unknown'
  if (percent > 90) return 'danger'
  if (percent >= 70) return 'warn'
  return 'ok'
}

export function buildQuotaTooltip(
  quota: ProviderQuota | null,
  provider: string | null | undefined,
  nowMs?: number,
): string {
  const effectiveProvider = normalizeSubscriptionProvider(quota?.provider ?? provider)
  const name = subscriptionDisplayName(effectiveProvider ?? quota?.provider ?? provider ?? 'Assinatura')
  const percent = highestKnownPercent(quota)
  const rejected = isQuotaRejected(quota)
  const lines: string[] = [name]

  if (quota && quota.windows.length > 0) {
    for (const win of sortQuotaWindows(quota.windows)) {
      const label = quotaWindowLabel(win)
      const valuePart =
        win.used_percent !== null
          ? `${Math.round(win.used_percent)}%`
          : quotaStatusLabel(win.status)
      const resetPart = formatQuotaRelease(win.resets_at, nowMs)
      lines.push(resetPart ? `${label}: ${valuePart} (${resetPart})` : `${label}: ${valuePart}`)
    }
  } else if (rejected) {
    lines.push('Assinatura temporariamente esgotada.')
  } else if (percent === null) {
    lines.push('Percentual de cota indisponível no momento.')
  }

  const age = formatQuotaAge(quota?.observed_at ?? null, quota?.age_seconds, nowMs)
  lines.push(`Atualização: ${age}`)
  return lines.join('\n')
}

export function QuotaRing({
  quota,
  provider,
  enabled = true,
  onClick,
  expanded = false,
  disabled = false,
  compact = false,
  nowMs,
}: QuotaRingProps) {
  if (!enabled) return null

  const effectiveProvider = normalizeSubscriptionProvider(quota?.provider ?? provider)
  const percent = highestKnownPercent(quota)
  const rejected = isQuotaRejected(quota)
  const confidence = quota?.confidence ?? 'unknown'
  const tone = quotaToneFor(percent, rejected)

  const progressRatio = rejected
    ? 1
    : percent !== null
      ? Math.max(0, Math.min(1, percent / 100))
      : 0
  const dashOffset = RING_CIRCUMFERENCE * (1 - progressRatio)

  const shortText =
    percent !== null
      ? `${Math.round(percent)}%`
      : rejected
        ? 'Esg.'
        : '?'

  const tooltip = buildQuotaTooltip(quota, effectiveProvider ?? provider, nowMs)
  const name = subscriptionDisplayName(effectiveProvider ?? quota?.provider ?? provider ?? 'Assinatura')
  const ariaLabel =
    percent !== null
      ? `Cota ${name}: ${Math.round(percent)}%`
      : rejected
        ? `Cota ${name}: esgotada`
        : `Cota ${name}: indisponível`

  return (
    <button
      type="button"
      className={`chip quota-ring quota-ring--${tone}${compact ? ' quota-ring--compact' : ''}`}
      data-testid="quota-ring-trigger"
      data-tone={tone}
      data-confidence={confidence}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      aria-label={ariaLabel}
      title={tooltip}
      disabled={disabled}
      onClick={onClick}
    >
      <svg
        className="quota-ring__svg"
        viewBox="0 0 16 16"
        width="14"
        height="14"
        aria-hidden="true"
      >
        <circle
          className="quota-ring__track"
          cx="8"
          cy="8"
          r={RING_RADIUS}
          fill="none"
          strokeWidth="2"
        />
        {(percent !== null || rejected) && (
          <circle
            className="quota-ring__arc"
            cx="8"
            cy="8"
            r={RING_RADIUS}
            fill="none"
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={RING_CIRCUMFERENCE}
            strokeDashoffset={dashOffset}
            transform="rotate(-90 8 8)"
          />
        )}
      </svg>
      <span className="quota-ring__value">{shortText}</span>
    </button>
  )
}
