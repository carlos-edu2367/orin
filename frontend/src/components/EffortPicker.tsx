import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import {
  effortLabel,
  nextEffortLevel,
  normalizeEffortLevel,
  type EffortLevel,
} from '../features/conversations/useEffortSelection'
import './EffortPicker.css'

export type EffortPickerProps = {
  supportedEfforts: readonly string[]
  defaultEffort?: string | null
  /** Explicit user choice, or `null` when using the model's automatic default. */
  value: string | null
  onChange: (effort: EffortLevel | null) => void
  /** Single-turn boost ("Pensar mais") state. */
  boost?: boolean
  onToggleBoost?: () => void
  disabled?: boolean
  compact?: boolean
}

function EffortGaugeIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2.5 11.5a6 6 0 1 1 11 0" />
      <path d="M8 9.2 10.8 6.4" />
      <circle cx="8" cy="9.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

function BoostSparkIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m4.5 9.5 3.5-3.5 3.5 3.5" />
      <path d="m4.5 13 3.5-3.5 3.5 3.5" />
    </svg>
  )
}

export function EffortPicker({
  supportedEfforts,
  defaultEffort = null,
  value,
  onChange,
  boost = false,
  onToggleBoost,
  disabled = false,
  compact = false,
}: EffortPickerProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const levels = useMemo<EffortLevel[]>(() => {
    const seen = new Set<EffortLevel>()
    const out: EffortLevel[] = []
    for (const item of supportedEfforts) {
      const normalized = normalizeEffortLevel(item)
      if (normalized && !seen.has(normalized)) {
        seen.add(normalized)
        out.push(normalized)
      }
    }
    return out
  }, [supportedEfforts])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (levels.length === 0) {
    return null
  }

  const normalizedDefault = normalizeEffortLevel(defaultEffort)
  const normalizedValue = normalizeEffortLevel(value)
  const effectiveLevel = normalizedValue ?? normalizedDefault ?? levels[0]
  const boostTarget = nextEffortLevel(normalizedValue, levels, normalizedDefault)
  const isExplicit = normalizedValue !== null

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    }
  }

  const triggerTitle = isExplicit
    ? `Esforço escolhido: ${effortLabel(effectiveLevel)} (${effectiveLevel})`
    : `Esforço no padrão do modelo: ${effortLabel(effectiveLevel)}`

  const boostTitle = boost
    ? `Pensar mais ativo para o próximo turno${boostTarget ? ` (sobe para ${effortLabel(boostTarget)})` : ''}`
    : `Elevar o esforço em um nível apenas no próximo turno${boostTarget ? ` (${effortLabel(boostTarget)})` : ''}`

  return (
    <div
      className={`effort-picker${compact ? ' effort-picker--compact' : ''}`}
      ref={containerRef}
      onKeyDown={handleKeyDown}
      data-testid="effort-picker"
    >
      <button
        type="button"
        className={`chip effort-picker__trigger${isExplicit ? ' is-explicit' : ''}`}
        data-testid="effort-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Esforço de raciocínio: ${effortLabel(effectiveLevel)}${isExplicit ? '' : ' (padrão)'}`}
        title={triggerTitle}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <EffortGaugeIcon />
        <span className="effort-picker__label">{effortLabel(effectiveLevel)}</span>
        <span className="chip__caret" aria-hidden="true">⌄</span>
      </button>

      {onToggleBoost && (
        <button
          type="button"
          className={`chip effort-picker__boost${boost ? ' is-active' : ''}`}
          data-testid="effort-boost-button"
          aria-pressed={boost}
          aria-label="Pensar mais neste turno"
          title={boostTitle}
          disabled={disabled}
          onClick={onToggleBoost}
        >
          <BoostSparkIcon />
          <span>Pensar mais</span>
        </button>
      )}

      {open && (
        <div className="picker-panel effort-picker__panel">
          <div className="effort-picker__header">
            <span className="effort-picker__title">Esforço de raciocínio</span>
            {isExplicit && (
              <button
                type="button"
                className="effort-picker__reset"
                onClick={() => {
                  onChange(null)
                  setOpen(false)
                }}
              >
                Usar padrão do modelo
              </button>
            )}
          </div>
          <ul role="listbox" aria-label="Nível de esforço" className="effort-picker__list">
            {levels.map((level) => {
              const isSelected = isExplicit ? level === normalizedValue : level === effectiveLevel
              const isDefault = level === normalizedDefault
              return (
                <li key={level}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    data-effort={level}
                    data-default={isDefault ? 'true' : undefined}
                    className={`picker-option effort-picker__option${isSelected ? ' is-selected' : ''}`}
                    onClick={() => {
                      onChange(level)
                      setOpen(false)
                    }}
                  >
                    <span className="picker-option__name">
                      <span>{effortLabel(level)}</span>
                      <span className="effort-picker__code">{level}</span>
                    </span>
                    {isDefault && <span className="picker-option__meta">padrão</span>}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}
