import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { displayTitle, requestDelete, sessionPath, startRename, togglePin, useSessionStore, type RenameSurface, type SessionRef } from './sessionStore'

export type SessionMenuState = { ref: SessionRef; x: number; y: number; where: RenameSurface } | null

/** State + opener for a right-click session menu. */
export function useSessionMenu(where: RenameSurface = 'nav') {
  const [menu, setMenu] = useState<SessionMenuState>(null)
  const open = useCallback((event: ReactMouseEvent, ref: SessionRef) => {
    event.preventDefault()
    event.stopPropagation()
    setMenu({ ref, x: event.clientX, y: event.clientY, where })
  }, [where])
  const close = useCallback(() => setMenu(null), [])
  return { menu, open, close }
}

export function PinIcon({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M9.8 1.6a.75.75 0 0 1 1.06 0l3.54 3.54a.75.75 0 0 1-.2 1.2l-2.27 1.14-1.86 3.2.53.53a.75.75 0 0 1-1.06 1.06L7.3 10.03 3.28 14.05a.6.6 0 1 1-.85-.85l4.02-4.02L4.1 6.83a.75.75 0 0 1 1.06-1.06l.53.53 3.2-1.86L10.03 2.17a.75.75 0 0 1-.23-.57Z" />
    </svg>
  )
}

/** Keyboard shortcuts on a focused session row: F2 renames, Delete deletes. */
export function handleSessionKeys(event: ReactKeyboardEvent, ref: SessionRef, where: RenameSurface = 'nav'): void {
  if (event.key === 'F2') {
    event.preventDefault()
    startRename(ref.id, where)
  } else if (event.key === 'Delete') {
    event.preventDefault()
    requestDelete(ref)
  }
}

export function SessionContextMenu({ menu, onClose }: { menu: SessionMenuState; onClose: () => void }) {
  const navigate = useNavigate()
  const store = useSessionStore()
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    if (!menu || !ref.current) {
      setPosition(null)
      return
    }
    const rect = ref.current.getBoundingClientRect()
    setPosition({
      left: Math.max(8, Math.min(menu.x, window.innerWidth - rect.width - 8)),
      top: Math.max(8, Math.min(menu.y, window.innerHeight - rect.height - 8)),
    })
    ref.current.querySelector<HTMLButtonElement>('button')?.focus()
  }, [menu])

  useEffect(() => {
    if (!menu) return
    const onPointer = (event: Event) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    const onBlur = () => onClose()
    document.addEventListener('mousedown', onPointer, true)
    document.addEventListener('contextmenu', onPointer, true)
    document.addEventListener('keydown', onKey)
    window.addEventListener('blur', onBlur)
    window.addEventListener('resize', onBlur)
    return () => {
      document.removeEventListener('mousedown', onPointer, true)
      document.removeEventListener('contextmenu', onPointer, true)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('resize', onBlur)
    }
  }, [menu, onClose])

  if (!menu) return null
  const pinned = store.pinned.some((item) => item.id === menu.ref.id)
  const isFriend = menu.ref.mode === 'friend'
  const run = (action: () => void) => () => {
    onClose()
    action()
  }
  const onMenuKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const items = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
    const index = items.indexOf(document.activeElement as HTMLButtonElement)
    const next = event.key === 'ArrowDown' ? (index + 1) % items.length : (index - 1 + items.length) % items.length
    items[next]?.focus()
  }

  return createPortal(
    <div
      ref={ref}
      className="session-menu"
      role="menu"
      aria-label={`Ações da sessão ${displayTitle(menu.ref, store)}`}
      data-testid="session-context-menu"
      style={position ? { left: position.left, top: position.top } : { left: menu.x, top: menu.y, visibility: 'hidden' }}
      onKeyDown={onMenuKey}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div className="session-menu__label" title={displayTitle(menu.ref, store)}>{displayTitle(menu.ref, store)}</div>
      <button type="button" role="menuitem" data-action="open" onClick={run(() => navigate(sessionPath(menu.ref)))}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        Abrir
      </button>
      <button type="button" role="menuitem" data-action="rename" onClick={run(() => startRename(menu.ref.id, menu.where))}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m10.5 2.5 3 3L6 13H3v-3l7.5-7.5Z" strokeLinejoin="round" /></svg>
        Renomear<kbd>F2</kbd>
      </button>
      <button type="button" role="menuitem" data-action="pin" onClick={run(() => togglePin({ ...menu.ref, title: displayTitle(menu.ref, store) }))}>
        <PinIcon size={14} />
        {pinned ? 'Desafixar do cabeçalho' : 'Fixar no cabeçalho'}
      </button>
      <hr />
      <button
        type="button"
        role="menuitem"
        data-action="delete"
        className="is-danger"
        disabled={isFriend}
        title={isFriend ? 'O Orin Friend não pode ser apagado' : undefined}
        onClick={run(() => requestDelete({ ...menu.ref, title: displayTitle(menu.ref, store) }))}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        Apagar<kbd>Del</kbd>
      </button>
    </div>,
    document.body,
  )
}
