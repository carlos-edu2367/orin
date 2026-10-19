import { useEffect, useRef } from 'react'

export type PushNotice = { id: string; message: string }

type PushNotificationToastProps = {
  notice: PushNotice | null
  onDismiss: () => void
  /** How long the toast stays before closing itself; 0 keeps it until dismissed. */
  durationMs?: number
}

/**
 * A notification the agent sent with PushNotification, shown while the window is open.
 * It is announced politely to screen readers and never takes the focus away from the composer.
 */
export function PushNotificationToast({ notice, onDismiss, durationMs = 8000 }: PushNotificationToastProps) {
  // The page re-renders while a reply streams; the timer must not restart each time it passes a new callback.
  const dismissRef = useRef(onDismiss)
  useEffect(() => {
    dismissRef.current = onDismiss
  }, [onDismiss])
  useEffect(() => {
    if (!notice || durationMs <= 0) return
    const timer = window.setTimeout(() => dismissRef.current(), durationMs)
    return () => window.clearTimeout(timer)
  }, [notice, durationMs])

  if (!notice) return null
  return (
    <div className="push-toast" role="status" aria-live="polite" data-testid="push-notification-toast">
      <span className="push-toast__glyph" aria-hidden="true">◉</span>
      <div className="push-toast__body">
        <strong>Orin</strong>
        <p>{notice.message}</p>
      </div>
      <button type="button" className="push-toast__close" onClick={onDismiss} aria-label="Fechar notificação">×</button>
    </div>
  )
}

/** A notification is shown once, and only while it is fresh: a replayed or old event never pops up again. */
export function isFreshNotice(occurredAt: string | undefined, now: number = Date.now(), maxAgeMs = 120_000): boolean {
  if (!occurredAt) return false
  const at = Date.parse(occurredAt)
  return Number.isFinite(at) && now - at <= maxAgeMs && at - now <= maxAgeMs
}
