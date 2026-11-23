import { useCallback, useEffect, useState } from 'react'

/** How often the "visto há X" ages advance on their own while the quota UI is mounted. */
export const QUOTA_AGE_TICK_MS = 30_000

/**
 * A wall-clock reading that advances every `QUOTA_AGE_TICK_MS`, so ages like "visto há 3 min" move without
 * new data. An injected `override` (tests, screenshots) wins and disables the timer. `bump` rereads the clock.
 */
export function useQuotaNow(override?: number, enabled = true): { nowMs: number; bump: () => void } {
  const [tickMs, setTickMs] = useState(() => Date.now())

  useEffect(() => {
    if (override !== undefined || !enabled) return
    const timer = window.setInterval(() => setTickMs(Date.now()), QUOTA_AGE_TICK_MS)
    return () => window.clearInterval(timer)
  }, [override, enabled])

  const bump = useCallback(() => setTickMs(Date.now()), [])
  return { nowMs: override ?? tickMs, bump }
}
