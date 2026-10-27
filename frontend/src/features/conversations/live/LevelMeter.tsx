import { useEffect, useRef, useState } from 'react'

export type LevelMeterProps = {
  level: () => number
  bars?: number
  /** Shown beside the bars so the signal is never conveyed by motion alone. */
  showVerdict?: boolean
  label?: string
  disabled?: boolean
}

type Verdict = 'silent' | 'low' | 'good' | 'loud'

const VERDICT_COPY: Record<Verdict, string> = {
  silent: 'Sem som chegando — confira o microfone escolhido',
  low: 'Sinal baixo — fale mais perto',
  good: 'Captando sua voz',
  loud: 'Sinal forte',
}

/** Live input meter: bars are driven straight from the DOM each frame, the verdict a few times a second. */
export function LevelMeter({ level, bars = 28, showVerdict = true, label = 'Nível do microfone', disabled = false }: LevelMeterProps) {
  const barsRef = useRef<HTMLDivElement | null>(null)
  const history = useRef<number[]>(Array.from({ length: bars }, () => 0))
  const peak = useRef(0)
  const silentSince = useRef<number | null>(null)
  const [verdict, setVerdict] = useState<Verdict>('silent')
  const [percent, setPercent] = useState(0)

  useEffect(() => {
    if (disabled) return
    let frame = 0
    let lastSample = 0
    const tick = (now: number) => {
      const value = Math.max(0, Math.min(1, level()))
      if (now - lastSample > 45) {
        lastSample = now
        history.current.push(value)
        if (history.current.length > bars) history.current.shift()
        const nodes = barsRef.current?.children
        if (nodes) {
          for (let i = 0; i < nodes.length; i += 1) {
            const v = history.current[i] ?? 0
            ;(nodes[i] as HTMLElement).style.transform = `scaleY(${Math.max(0.06, v).toFixed(3)})`
            ;(nodes[i] as HTMLElement).dataset.hot = v > 0.82 ? 'true' : 'false'
          }
        }
      }
      peak.current = Math.max(peak.current * 0.97, value)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    const verdictTimer = window.setInterval(() => {
      const p = peak.current
      setPercent(Math.round(p * 100))
      if (p < 0.06) {
        silentSince.current ??= Date.now()
        setVerdict(Date.now() - silentSince.current > 2500 ? 'silent' : 'low')
      } else {
        silentSince.current = null
        setVerdict(p < 0.28 ? 'low' : p > 0.9 ? 'loud' : 'good')
      }
    }, 400)
    return () => {
      cancelAnimationFrame(frame)
      window.clearInterval(verdictTimer)
    }
  }, [level, bars, disabled])

  return (
    <div className="level-meter" data-verdict={disabled ? 'off' : verdict}>
      <div
        className="level-meter__bars"
        ref={barsRef}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={disabled ? 0 : percent}
        data-testid="live-mic-meter"
      >
        {Array.from({ length: bars }, (_, index) => (
          <span key={index} className="level-meter__bar" />
        ))}
      </div>
      {showVerdict && (
        <span className="level-meter__verdict" data-testid="live-mic-verdict">
          {disabled ? 'Microfone desligado' : VERDICT_COPY[verdict]}
        </span>
      )}
    </div>
  )
}
