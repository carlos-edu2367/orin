import { useState, type DragEvent, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import { SessionContextMenu, PinIcon, handleSessionKeys, useSessionMenu } from './SessionContextMenu'
import { SessionRenameInput } from './SessionActionsHost'
import {
  SESSION_DRAG_TYPE, displayTitle, isRenaming, pinSession, sessionPath, showSessionToast, startRename, unpinSession,
  useSessionStore, type SessionRef,
} from './sessionStore'

export function readDraggedSession(event: DragEvent): SessionRef | null {
  try {
    const raw: unknown = JSON.parse(event.dataTransfer.getData(SESSION_DRAG_TYPE) || 'null')
    if (!raw || typeof raw !== 'object') return null
    const data = raw as Record<string, unknown>
    if (typeof data.id !== 'string' || !data.id) return null
    return {
      id: data.id,
      title: typeof data.title === 'string' ? data.title : 'Conversa',
      mode: data.mode === 'code' || data.mode === 'friend' ? data.mode : 'chat',
      projectId: typeof data.projectId === 'string' && data.projectId ? data.projectId : null,
    }
  } catch {
    return null
  }
}

export function startSessionDrag(event: DragEvent, ref: SessionRef): void {
  event.dataTransfer.setData(SESSION_DRAG_TYPE, JSON.stringify(ref))
  event.dataTransfer.setData('text/plain', ref.title)
  event.dataTransfer.effectAllowed = 'copyMove'
}

function carriesSession(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes(SESSION_DRAG_TYPE)
}

function ModeTag({ mode }: { mode?: SessionRef['mode'] }) {
  if (mode === 'code') return <span className="session-tab__mode is-code" aria-hidden="true">&gt;_</span>
  if (mode === 'friend') return <span className="session-tab__mode is-friend" aria-hidden="true">◈</span>
  return (
    <span className="session-tab__mode" aria-hidden="true">
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6l-3.5 2.5v-2.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </span>
  )
}

export type PinnedSessionTabsProps = {
  client: ApiClient
  /** The conversation open on this page, if any. */
  current?: SessionRef | null
  /** Shown when nothing is pinned (the chat page passes its title). */
  fallback?: ReactNode
}

/** The pinned sessions as tabs in the header, plus the "Fixar" action for the open session. */
export function PinnedSessionTabs({ client, current = null, fallback = null }: PinnedSessionTabsProps) {
  const store = useSessionStore()
  const navigate = useNavigate()
  const location = useLocation()
  const { menu, open: openMenu, close: closeMenu } = useSessionMenu('tab')
  const [dropAt, setDropAt] = useState<number | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)

  const tabs = store.pinned.filter((item) => !store.hidden.includes(item.id))
  const segments = location.pathname.split('/')
  // The URL changes before the page has loaded the next conversation, so it decides first.
  const activeId = tabs.find((item) => segments.includes(encodeURIComponent(item.id)))?.id ?? current?.id ?? null
  const currentPinned = current ? tabs.some((item) => item.id === current.id) : true
  const currentRef = current ? { ...current, title: displayTitle(current, store) } : null

  const indexFor = (event: DragEvent<HTMLElement>, index: number, element: HTMLElement = event.currentTarget) => {
    const rect = element.getBoundingClientRect()
    return event.clientX < rect.left + rect.width / 2 ? index : index + 1
  }
  const onDragOverStrip = (event: DragEvent<HTMLElement>) => {
    if (!carriesSession(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    if (event.target === event.currentTarget) setDropAt(tabs.length)
  }
  const onDrop = (event: DragEvent<HTMLElement>) => {
    const ref = readDraggedSession(event)
    // Read the slot from the drop itself: the last dragover's state may not be rendered yet.
    const over = (event.target as HTMLElement | null)?.closest?.<HTMLElement>('[data-tab-index]')
    const at = over ? indexFor(event, Number(over.dataset.tabIndex), over) : tabs.length
    setDropAt(null)
    setDragging(null)
    if (!ref) return
    event.preventDefault()
    const wasPinned = store.pinned.some((item) => item.id === ref.id)
    // Insert before the first visible tab at or after the drop slot (other than the dragged one).
    const before = tabs.slice(at).find((item) => item.id !== ref.id)
    const storeIndex = before ? store.pinned.filter((item) => item.id !== ref.id).findIndex((item) => item.id === before.id) : -1
    pinSession(ref, storeIndex < 0 ? undefined : storeIndex)
    if (!wasPinned) showSessionToast('Sessão fixada no cabeçalho')
  }
  const dropTarget = dropAt !== null || Boolean(dragging)

  if (tabs.length === 0) {
    return (
      <div
        className={`session-tabs session-tabs--empty${dropAt !== null ? ' is-drop-target' : ''}`}
        data-testid="session-tabs"
        onDragOver={onDragOverStrip}
        onDragEnter={(event) => { if (carriesSession(event)) setDropAt(0) }}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropAt(null) }}
        onDrop={onDrop}
      >
        {dropAt !== null ? <span className="session-tabs__hint">Solte para fixar no cabeçalho</span> : fallback}
        {currentRef && dropAt === null && (
          <button
            type="button"
            className="session-tabs__pin-current is-icon"
            data-testid="session-pin-current"
            title="Fixar esta sessão no cabeçalho"
            aria-label="Fixar esta sessão no cabeçalho"
            onClick={() => { pinSession(currentRef); showSessionToast('Sessão fixada no cabeçalho') }}
          >
            <PinIcon />
          </button>
        )}
      </div>
    )
  }

  return (
    <>
    {/* The title keeps its place in the top bar; the tabs get a full-width row below it (see sessions.css). */}
    <span className="session-tabs__title">{fallback}</span>
    <div className="session-bar" data-testid="session-tabs">
      <div
        className={`session-tabs${dropTarget ? ' is-drop-target' : ''}`}
        role="tablist"
        aria-label="Sessões fixadas"
        onDragOver={onDragOverStrip}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropAt(null) }}
        onDrop={onDrop}
      >
        {tabs.map((tab, index) => {
          const title = displayTitle(tab, store)
          const ref = { ...tab, title }
          const active = tab.id === activeId
          const renaming = isRenaming(store, tab.id, 'tab')
          const cls = ['session-tab', active && 'is-active', dragging === tab.id && 'is-dragging',
            dropAt === index && 'drop-before', dropAt === index + 1 && index === tabs.length - 1 && 'drop-after'].filter(Boolean).join(' ')
          return (
            <div
              key={tab.id}
              className={cls}
              role="tab"
              tabIndex={0}
              aria-selected={active}
              title={title}
              data-testid={`session-tab-${tab.id}`}
              data-tab-index={index}
              draggable={!renaming}
              onClick={() => { if (!renaming) navigate(sessionPath(tab)) }}
              onKeyDown={(event) => {
                if (renaming) return
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  navigate(sessionPath(tab))
                  return
                }
                handleSessionKeys(event, ref, 'tab')
              }}
              onMouseDown={(event) => { if (event.button === 1) event.preventDefault() }}
              onAuxClick={(event) => {
                if (event.button !== 1) return
                event.preventDefault()
                unpinSession(tab.id)
              }}
              onDoubleClick={() => startRename(tab.id, 'tab')}
              onContextMenu={(event) => openMenu(event, ref)}
              onDragStart={(event) => { startSessionDrag(event, ref); setDragging(tab.id) }}
              onDragEnd={() => { setDragging(null); setDropAt(null) }}
              onDragOver={(event) => {
                if (!carriesSession(event)) return
                event.preventDefault()
                event.stopPropagation()
                setDropAt(indexFor(event, index))
              }}
            >
              <ModeTag mode={tab.mode} />
              {renaming
                ? <SessionRenameInput client={client} session={ref} className="session-tab__rename" />
                : <span className="session-tab__title">{title}</span>}
              <span className="session-tab__state" data-state={store.states[tab.id] ?? 'idle'} aria-hidden="true" />
              <button
                type="button"
                className="session-tab__close"
                aria-label={`Desafixar ${title}`}
                title="Desafixar (a sessão continua na lateral)"
                data-testid={`session-tab-close-${tab.id}`}
                onClick={(event) => { event.stopPropagation(); unpinSession(tab.id) }}
              >
                ✕
              </button>
            </div>
          )
        })}
      </div>
      {currentRef && !currentPinned && (
        <>
          <span className="session-tabs__divider" aria-hidden="true" />
          <button
            type="button"
            className="session-tabs__pin-current"
            data-testid="session-pin-current"
            title={`Fixar “${currentRef.title}” no cabeçalho`}
            onClick={() => { pinSession(currentRef); showSessionToast('Sessão fixada no cabeçalho') }}
          >
            <PinIcon /> Fixar esta
          </button>
        </>
      )}
      <SessionContextMenu menu={menu} onClose={closeMenu} />
    </div>
    </>
  )
}
