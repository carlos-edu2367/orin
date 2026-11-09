import { useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import { startSessionDrag } from './PinnedSessionTabs'
import { SessionRenameInput } from './SessionActionsHost'
import { PinIcon, handleSessionKeys } from './SessionContextMenu'
import { displayTitle, isRenaming, startRename, useSessionStore, type SessionRef } from './sessionStore'

export type SessionNavRowProps = {
  client: ApiClient
  session: SessionRef
  to: string
  state: string
  badge: ReactNode
  /** Relative time label; omitted for rows that do not show it. */
  time?: string | null
  extraClassName?: string
  onNavigate?: () => void
  onOpenMenu: (event: ReactMouseEvent, ref: SessionRef) => void
}

/** A conversation row in the sidebar: link, right-click menu, F2/Delete, double-click rename and drag to the header. */
export function SessionNavRow({ client, session, to, state, badge, time, extraClassName, onNavigate, onOpenMenu }: SessionNavRowProps) {
  const store = useSessionStore()
  const [dragging, setDragging] = useState(false)
  const title = displayTitle(session, store)
  const ref = { ...session, title }
  const pinned = store.pinned.some((item) => item.id === session.id)
  const base = `project-navigation__chat${extraClassName ? ` ${extraClassName}` : ''}`
  const tail = (
    <>
      {pinned && (
        <span className="project-navigation__chat-pin" title="Fixada no cabeçalho" data-testid={`conversation-pinned-${session.id}`}>
          <PinIcon size={11} />
        </span>
      )}
      {time != null && state !== 'running' && (
        <span className="project-navigation__chat-time" data-testid={`conversation-time-${session.id}`}>{time}</span>
      )}
      <span className="project-navigation__chat-state" data-testid={`conversation-state-${session.id}`} data-state={state} aria-hidden="true" />
    </>
  )

  if (isRenaming(store, session.id, 'nav')) {
    return (
      <div className={`${base} is-renaming`} data-session-row={session.id}>
        {badge}
        <SessionRenameInput client={client} session={ref} />
        {tail}
      </div>
    )
  }

  return (
    <NavLink
      to={to}
      title={title}
      data-session-row={session.id}
      draggable
      onClick={onNavigate}
      onContextMenu={(event) => onOpenMenu(event, ref)}
      onKeyDown={(event) => handleSessionKeys(event, ref)}
      onDragStart={(event) => { startSessionDrag(event, ref); setDragging(true) }}
      onDragEnd={() => setDragging(false)}
      className={({ isActive }) => `${base}${isActive ? ' is-active' : ''}${dragging ? ' is-dragging' : ''}`}
    >
      {badge}
      <span
        className="project-navigation__chat-title"
        title={title}
        onDoubleClick={(event) => {
          event.preventDefault()
          startRename(session.id, 'nav')
        }}
      >
        {title}
      </span>
      {tail}
    </NavLink>
  )
}
