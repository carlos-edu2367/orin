import { useId, type JSX } from 'react'

export type OrinMascotVariant = 'flat' | 'soft3d' | 'waving' | 'mini'
export type OrinMascotStatusDot = 'online' | 'busy' | 'none'

const DEFAULT_MASCOT_COLOR = 'var(--orin-accent, #B4A4EF)'

export type OrinMascotProps = {
  size?: number | string
  variant?: OrinMascotVariant
  statusDot?: OrinMascotStatusDot
  color?: string
  animated?: boolean
  ariaLabel?: string
  ariaHidden?: boolean
  className?: string
}

const CANONICAL_BODY_PATH = `
  M 55 46
  C 48 29, 58 21, 68 35
  C 74 14, 95 18, 87 37
  C 115 38, 137 66, 139 96
  C 142 125, 121 138, 81 139
  C 42 140, 20 128, 21 102
  C 22 77, 35 55, 55 46
  Z
`

function normalizeSize(size: number | string | undefined): number {
  if (typeof size === 'number') {
    return Number.isFinite(size) && size > 0 ? size : 28
  }
  if (typeof size === 'string') {
    const parsed = Number.parseFloat(size)
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 28
  }
  return 28
}

export function OrinMascot({
  size = 28,
  variant = 'flat',
  statusDot = 'none',
  color = DEFAULT_MASCOT_COLOR,
  animated = false,
  ariaLabel = 'Mascote Orin',
  ariaHidden = false,
  className,
}: OrinMascotProps): JSX.Element {
  const rawId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const gradId = `orin-mascot-grad-${rawId}`
  const pxSize = normalizeSize(size)
  const validVariant: OrinMascotVariant =
    variant === 'soft3d' || variant === 'waving' || variant === 'mini' ? variant : 'flat'
  const validStatus: OrinMascotStatusDot =
    statusDot === 'online' || statusDot === 'busy' ? statusDot : 'none'

  const use3dFill = validVariant === 'soft3d' || validVariant === 'waving'
  const bodyFill = use3dFill ? `url(#${gradId})` : color
  const isCustomColor = color !== DEFAULT_MASCOT_COLOR
  const gradStopStart = isCustomColor
    ? `color-mix(in srgb, ${color} 68%, white)`
    : 'var(--orin-accent-soft, #DDD5FA)'
  const gradStopEnd = isCustomColor
    ? `color-mix(in srgb, ${color} 78%, #181523)`
    : 'var(--orin-accent-strong, #8F7AE0)'

  return (
    <span
      className={`orin-mascot orin-mascot--${validVariant}${animated ? ' is-animated' : ''}${className ? ` ${className}` : ''}`}
      style={{ width: `${pxSize}px`, height: `${pxSize}px` }}
      data-testid="orin-mascot"
      data-variant={validVariant}
      data-status-dot={validStatus}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={pxSize}
        height={pxSize}
        viewBox="0 0 160 160"
        fill="none"
        role={ariaHidden ? undefined : 'img'}
        aria-label={ariaHidden ? undefined : ariaLabel}
        aria-hidden={ariaHidden ? 'true' : undefined}
        data-variant={validVariant}
        className="orin-mascot__svg"
      >
        {use3dFill && (
          <defs>
            <radialGradient id={gradId} cx="36%" cy="30%" r="68%" fx="36%" fy="30%">
              <stop offset="0%" stopColor={gradStopStart} />
              <stop offset="55%" stopColor={color} />
              <stop offset="100%" stopColor={gradStopEnd} />
            </radialGradient>
          </defs>
        )}

        {use3dFill && (
          <ellipse
            cx="80"
            cy="148"
            rx="44"
            ry="6"
            fill="rgba(0, 0, 0, 0.34)"
            data-mascot-part="shadow"
          />
        )}

        <path d={CANONICAL_BODY_PATH} fill={bodyFill} data-mascot-part="body" />

        {validVariant === 'waving' && (
          <g data-mascot-part="waving-arm">
            <path
              d="M 129 92 C 144 82, 154 68, 146 58 C 139 50, 128 62, 123 76 Z"
              fill={bodyFill}
            />
            <line
              x1="134"
              y1="30"
              x2="144"
              y2="20"
              stroke={color}
              strokeWidth="3.5"
              strokeLinecap="round"
              data-mascot-part="sparkle"
            />
            <line
              x1="145"
              y1="42"
              x2="156"
              y2="38"
              stroke={color}
              strokeWidth="3.5"
              strokeLinecap="round"
              data-mascot-part="sparkle"
            />
            <line
              x1="122"
              y1="22"
              x2="126"
              y2="11"
              stroke={color}
              strokeWidth="3.5"
              strokeLinecap="round"
              data-mascot-part="sparkle"
            />
          </g>
        )}

        <ellipse
          cx="61"
          cy="88"
          rx="5"
          ry="11"
          fill="#282333"
          transform="rotate(-5 61 88)"
          data-mascot-part="eye"
        />
        <ellipse
          cx="101"
          cy="86"
          rx="5"
          ry="11"
          fill="#282333"
          transform="rotate(-5 101 86)"
          data-mascot-part="eye"
        />

        <path
          d="M 76 105 Q 81 112 87 104"
          stroke="#282333"
          strokeWidth="3"
          strokeLinecap="round"
          data-mascot-part="smile"
        />
      </svg>

      {validStatus !== 'none' && (
        <span
          className={`orin-mascot__status-dot orin-mascot__status-dot--${validStatus}`}
          data-testid="orin-mascot-status-dot"
          aria-hidden="true"
        />
      )}
    </span>
  )
}
