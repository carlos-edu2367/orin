import { useEffect, useRef, type ReactNode } from 'react'

/** A non-modal detail panel: the room behind it remains visible. */
export function SettingsDrawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null)
  const restoreTo = useRef<HTMLElement | null>(null)

  useEffect(() => {
    restoreTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panel.current?.focus()
    return () => restoreTo.current?.focus()
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return <div className="settings-drawer" role="region" aria-label={title} tabIndex={-1} ref={panel}>
    <div className="settings-drawer__head">
      <h2>{title}</h2>
      <button type="button" className="button--quiet" onClick={onClose}>Fechar</button>
    </div>
    <div className="settings-drawer__body">{children}</div>
  </div>
}
