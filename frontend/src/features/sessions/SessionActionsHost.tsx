import { useEffect, useMemo, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import { deleteConversation } from '../../api/conversations'
import {
  cancelDelete, cancelRename, commitRename, confirmDelete, dismissSessionToast, flushDeleteOnUnload, sessionPath, undoDelete,
  useSessionStore, type SessionRef,
} from './sessionStore'

/** Inline title editor: Enter saves, Escape cancels, leaving the field saves. */
export function SessionRenameInput({ client, session, className }: { client: ApiClient; session: SessionRef; className?: string }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  useEffect(() => {
    const input = inputRef.current
    if (!input) return
    input.focus()
    input.select()
  }, [])
  const save = () => {
    if (done.current) return
    done.current = true
    void commitRename(client, session, inputRef.current?.value ?? '')
  }
  return (
    <input
      ref={inputRef}
      className={`session-rename-input${className ? ` ${className}` : ''}`}
      defaultValue={session.title}
      maxLength={160}
      aria-label="Novo título da sessão"
      data-testid={`session-rename-input-${session.id}`}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
      }}
      onMouseDown={(event) => event.stopPropagation()}
      onDragStart={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key === 'Enter') {
          event.preventDefault()
          save()
        } else if (event.key === 'Escape') {
          event.preventDefault()
          done.current = true
          cancelRename()
        }
      }}
      onBlur={save}
    />
  )
}

function isOpen(pathname: string, id: string): boolean {
  return pathname.split('/').includes(encodeURIComponent(id))
}

/** Confirmation dialog and toast for session actions. Mounted once, by the sidebar. */
export function SessionActionsHost({ client }: { client: ApiClient }) {
  const store = useSessionStore()
  const navigate = useNavigate()
  const location = useLocation()
  const confirmRef = useRef<HTMLButtonElement>(null)
  const remove = useMemo(() => (id: string) => deleteConversation(client, id), [client])

  useEffect(() => {
    window.addEventListener('pagehide', flushDeleteOnUnload)
    return () => window.removeEventListener('pagehide', flushDeleteOnUnload)
  }, [])

  useEffect(() => {
    if (!store.confirmDelete) return
    confirmRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        cancelDelete()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [store.confirmDelete])

  const accept = () => {
    let wasOpen = false
    const pathname = location.pathname
    confirmDelete(remove, {
      onLeave: (ref) => {
        wasOpen = isOpen(pathname, ref.id)
        if (!wasOpen) return
        const next = store.pinned.find((item) => item.id !== ref.id)
        navigate(next ? sessionPath(next) : '/')
      },
      onUndo: (ref) => {
        if (wasOpen) navigate(sessionPath(ref))
      },
    })
  }

  const target = store.confirmDelete
  return createPortal(
    <>
      {target && (
        <div className="session-dialog__backdrop" data-testid="session-delete-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) cancelDelete() }}>
          <div className="session-dialog" role="alertdialog" aria-modal="true" aria-labelledby="session-dialog-title" data-testid="session-delete-dialog">
            <h2 id="session-dialog-title">Apagar sessão?</h2>
            <p>
              <strong>{target.title}</strong> e todo o histórico dela vão ser apagados. Os arquivos que ela criou na pasta do projeto ficam onde estão.
            </p>
            <div className="session-dialog__actions">
              <button type="button" className="session-dialog__cancel" onClick={cancelDelete}>Cancelar</button>
              <button type="button" ref={confirmRef} className="session-dialog__danger" data-testid="session-delete-confirm" onClick={accept}>Apagar</button>
            </div>
          </div>
        </div>
      )}
      {store.toast && (
        <div className={`session-toast${store.toast.tone === 'error' ? ' is-error' : ''}`} role="status" data-testid="session-toast" key={store.toast.key}>
          <span>{store.toast.text}</span>
          {store.toast.undo ? (
            <button type="button" data-testid="session-toast-undo" onClick={undoDelete}>Desfazer</button>
          ) : (
            <button type="button" aria-label="Fechar aviso" onClick={dismissSessionToast}>✕</button>
          )}
        </div>
      )}
    </>,
    document.body,
  )
}
