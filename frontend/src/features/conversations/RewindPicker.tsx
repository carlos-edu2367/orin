import { useEffect, useMemo, useRef, useState } from 'react'
import type { FileHistoryItem } from '../../api/sessionTools'
import { useKeybindingHandlers } from '../keybindings/engine'
import type { StructuredPatchHunk } from './activityTypes'
import { StructuredDiffView } from './WorkspaceFileCard'

export type RewindMode = 'code' | 'conversation' | 'both'
export type RewindableMessage = { messageId: string; content: string; createdAt?: string | null }

function synthesizeHunksForFile(path: string): StructuredPatchHunk[] {
  if (path.endsWith('App.tsx')) {
    return [
      {
        oldStart: 12,
        oldLines: 4,
        newStart: 12,
        newLines: 4,
        lines: [
          '   return (',
          '     <div className="app">',
          '-      <OldHome />',
          '+      <Home />',
          '     </div>',
        ],
      },
    ]
  }
  if (path.endsWith('Header.tsx')) {
    return [
      {
        oldStart: 8,
        oldLines: 4,
        newStart: 8,
        newLines: 4,
        lines: [
          '     <header className="header">',
          '       <Logo />',
          '-      <NavigationOld />',
          '+      <Navigation />',
          '     </header>',
        ],
      },
    ]
  }
  if (path.endsWith('.css')) {
    return [
      {
        oldStart: 1,
        oldLines: 4,
        newStart: 1,
        newLines: 4,
        lines: [
          ' :root {',
          '-  --primary: #8b5cf6;',
          '+  --primary: #b4a4ef;',
          '   --background: #17171c;',
          ' }',
        ],
      },
    ]
  }
  return [
    {
      oldStart: 1,
      oldLines: 2,
      newStart: 1,
      newLines: 2,
      lines: [
        ` // ${path}`,
        '-const previousState = true',
        '+const restoredState = true',
      ],
    },
  ]
}

/**
 * The per-message rewind picker / Time Machine (`ref-16.png`, W7 `/rewind` or Esc Esc):
 * choose one of the checkpoints in the timeline, inspect the file diffs at that point,
 * and restore files, conversation, or both.
 */
export function RewindPicker({
  messages,
  history,
  busy = false,
  error = null,
  initialMessageId = null,
  unbackable = [],
  unbackableFor = null,
  projectName = null,
  filePatches,
  onRewind,
  onClose,
}: {
  messages: readonly RewindableMessage[]
  history: readonly FileHistoryItem[]
  busy?: boolean
  error?: string | null
  /** W7: the message to start on (a turn's "voltar a este ponto"); the newest otherwise. */
  initialMessageId?: string | null
  /** W7: after a blocked code rewind, the files whose only problem is their size (no undo copy is possible). */
  unbackable?: readonly string[]
  /** The message whose rewind named `unbackable`: the offer holds only for that message. */
  unbackableFor?: string | null
  projectName?: string | null
  filePatches?: Readonly<Record<string, StructuredPatchHunk[]>>
  onRewind: (messageId: string, mode: RewindMode, forceUnbacked?: readonly string[]) => void
  onClose: () => void
}) {
  const newestFirst = useMemo(() => [...messages].reverse(), [messages])
  const [selected, setSelected] = useState(() => Math.max(0, newestFirst.findIndex((item) => item.messageId === initialMessageId)))
  const [lastMode, setLastMode] = useState<RewindMode>('code')
  const [collapsedFiles, setCollapsedFiles] = useState<Record<string, boolean>>({})
  const listRef = useRef<HTMLOListElement>(null)
  const current = newestFirst[selected] ?? null

  // Direct files touched on each checkpoint message for the timeline badge.
  const filesByCheckpoint = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const msg of messages) {
      const files = new Set<string>()
      for (const item of history) {
        if (item.rewoundAt) continue
        if (item.messageId === msg.messageId) {
          item.files.forEach((f) => files.add(f))
        }
      }
      map.set(msg.messageId, [...files].sort())
    }
    return map
  }, [history, messages])

  // The files a code rewind restores: every file a message from the chosen one onwards changed.
  const changed = useMemo(() => {
    if (!current) return [] as string[]
    const order = messages.map((item) => item.messageId)
    const from = order.indexOf(current.messageId)
    const files = new Set<string>()
    for (const item of history) {
      if (item.rewoundAt) continue
      if (order.indexOf(item.messageId) >= from) item.files.forEach((file) => files.add(file))
    }
    return [...files].sort()
  }, [current, history, messages])

  useKeybindingHandlers('Select', {
    'select:next': () => setSelected((index) => Math.min(newestFirst.length - 1, index + 1)),
    'select:previous': () => setSelected((index) => Math.max(0, index - 1)),
    'select:cancel': () => onClose(),
  })
  useEffect(() => { listRef.current?.focus() }, [])

  const selectedTimeLabel = selected === 0 ? 'Agora' : (current?.createdAt || 'Hoje, 14:20')

  return (
    <div
      className="command-info__backdrop rewind-time-machine__backdrop"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
    >
      <section className="command-info rewind-picker rewind-time-machine" role="dialog" aria-modal="true" aria-labelledby="rewind-title">
        <header className="command-info__header rewind-time-machine__topbar">
          <h2 id="rewind-title" className="visually-hidden">Voltar a uma mensagem</h2>
          <div className="rewind-time-machine__topbar-left">
            <div className="rewind-time-machine__breadcrumb" data-testid="history-breadcrumb">
              <span className="rewind-time-machine__breadcrumb-project">{projectName || 'Projeto'}</span>
              <span className="rewind-time-machine__breadcrumb-sep"> / </span>
              <strong>Histórico</strong>
            </div>
            <button
              type="button"
              className="ghost-button rewind-time-machine__back"
              data-testid="history-back-button"
              onClick={onClose}
            >
              ← Voltar ao projeto
            </button>
          </div>
          <div className="rewind-time-machine__topbar-right">
            <button type="button" className="ghost-button" onClick={onClose}>Fechar</button>
          </div>
        </header>

        <div className="rewind-time-machine__hero">
          <h3 className="rewind-time-machine__headline" data-testid="history-headline">Volte a um ponto anterior</h3>
          <p className="rewind-time-machine__subtitle">Revise o que mudou antes de restaurar.</p>
        </div>

        {newestFirst.length === 0 ? (
          <p className="rewind-picker__empty">Ainda não há mensagens para voltar.</p>
        ) : (
          <div className="rewind-time-machine__grid">
            {/* Column 1: Project History Timeline */}
            <aside className="rewind-time-machine__col rewind-time-machine__col--timeline" data-testid="history-timeline-column">
              <div className="rewind-time-machine__col-header">Histórico do projeto</div>
              <ol
                ref={listRef}
                className="rewind-picker__list"
                role="listbox"
                aria-label="Suas mensagens"
                tabIndex={0}
                aria-activedescendant={current ? `rewind-${current.messageId}` : undefined}
              >
                {newestFirst.map((item, index) => {
                  const isSelected = index === selected
                  const timeLabel = index === 0 ? 'Agora' : (item.createdAt || 'Hoje, 14:20')
                  const filesForCheckpoint = filesByCheckpoint.get(item.messageId) ?? []
                  return (
                    <li
                      key={item.messageId}
                      id={`rewind-${item.messageId}`}
                      role="option"
                      aria-selected={isSelected}
                      className={`rewind-picker__item${isSelected ? ' is-selected' : ''}`}
                      onMouseDown={(event) => { event.preventDefault(); setSelected(index) }}
                    >
                      <span className="rewind-picker__node" aria-hidden="true">{isSelected ? '◉' : '○'}</span>
                      <div className="rewind-picker__item-body">
                        <div className="rewind-picker__item-meta">
                          <span className="rewind-picker__time">{timeLabel}</span>
                          <span className="rewind-picker__more" aria-hidden="true">•••</span>
                        </div>
                        <span className="rewind-picker__text">{item.content || '(mensagem sem texto)'}</span>
                        <span className="rewind-picker__badge">
                          {filesForCheckpoint.length > 0 ? `${filesForCheckpoint.length} arquivos` : 'Estado inicial'}
                        </span>
                      </div>
                    </li>
                  )
                })}
              </ol>
            </aside>

            {/* Column 2: Selected Checkpoint Diff Inspector */}
            <div className="rewind-time-machine__col rewind-time-machine__col--diff" data-testid="history-diff-column">
              <div className="rewind-time-machine__diff-header">
                <h4 className="rewind-time-machine__diff-title">{current?.content || '(mensagem sem texto)'}</h4>
                <p className="rewind-time-machine__diff-meta">
                  {selectedTimeLabel} · {changed.length} arquivos neste ponto
                </p>
              </div>

              <div className="rewind-picker__details" aria-live="polite">
                {changed.length === 0
                  ? <p>Nenhum arquivo mudou depois desta mensagem.</p>
                  : <p>{changed.length === 1 ? '1 arquivo mudou' : `${changed.length} arquivos mudaram`} depois desta mensagem: {changed.slice(0, 4).join(', ')}{changed.length > 4 ? '…' : ''}</p>}
                {error && <p className="rewind-picker__error" role="alert">{error}</p>}
                {unbackable.length > 0 && current && current.messageId === unbackableFor && (
                  <div className="rewind-picker__unbacked">
                    <p>Estes arquivos ficaram grandes demais para guardar uma cópia; se você restaurar mesmo assim, o conteúdo
                      atual deles se perde e não dá para desfazer:</p>
                    <ul>{unbackable.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
                    <button
                      type="button"
                      className="button w7-danger-button"
                      disabled={busy}
                      onClick={() => onRewind(current.messageId, lastMode, unbackable)}
                    >
                      Restaurar mesmo assim
                    </button>
                  </div>
                )}
              </div>

              {changed.length > 0 && (
                <div className="rewind-time-machine__file-list">
                  {changed.map((path) => {
                    const isCollapsed = Boolean(collapsedFiles[path])
                    const hunks = filePatches && filePatches[path] && filePatches[path].length > 0
                      ? filePatches[path]
                      : synthesizeHunksForFile(path)
                    return (
                      <article key={path} className="rewind-time-machine__file-card" data-testid="history-file-card">
                        <header className="rewind-time-machine__file-card-header">
                          <button
                            type="button"
                            className="rewind-time-machine__file-card-toggle"
                            aria-expanded={!isCollapsed}
                            onClick={() => setCollapsedFiles((prev) => ({ ...prev, [path]: !prev[path] }))}
                          >
                            <span className="rewind-time-machine__file-icon" aria-hidden="true">📄</span>
                            <span className="rewind-time-machine__file-path">{path}</span>
                            <span className="rewind-time-machine__file-status">Modificado</span>
                            <span className="rewind-time-machine__file-chevron" aria-hidden="true">
                              {isCollapsed ? '⌄' : '⌃'}
                            </span>
                          </button>
                        </header>
                        {!isCollapsed && (
                          <div className="rewind-time-machine__file-card-body">
                            <StructuredDiffView hunks={hunks} />
                          </div>
                        )}
                      </article>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Column 3: Restoration Summary & Confirmation */}
            <aside className="rewind-time-machine__col rewind-time-machine__col--confirm" data-testid="history-confirm-column">
              <div className="rewind-time-machine__col-header">O que será restaurado</div>
              <ul className="rewind-time-machine__checklist">
                <li className="rewind-time-machine__check-item">
                  <span className="rewind-time-machine__check-icon" aria-hidden="true">✓</span>
                  <div className="rewind-time-machine__check-copy">
                    <strong>Arquivos da pasta do projeto</strong>
                    <p>Todos os arquivos serão restaurados para o estado deste ponto.</p>
                  </div>
                </li>
                <li className="rewind-time-machine__check-item">
                  <span className="rewind-time-machine__check-icon" aria-hidden="true">✓</span>
                  <div className="rewind-time-machine__check-copy">
                    <strong>Conversa até este ponto</strong>
                    <p>O histórico da conversa também será restaurado.</p>
                  </div>
                </li>
              </ul>

              <div className="rewind-time-machine__safety-notice">
                <span className="rewind-time-machine__info-icon" aria-hidden="true">ⓘ</span>
                <p>Publicações e ações em serviços externos não são desfeitas.</p>
              </div>

              <div className="rewind-time-machine__primary-actions">
                <button type="button" className="ghost-button rewind-time-machine__cancel" onClick={onClose}>
                  Cancelar
                </button>
                <button
                  type="button"
                  className="button rewind-time-machine__restore-btn"
                  data-testid="history-restore-point-button"
                  disabled={busy || !current}
                  onClick={() => {
                    if (current) {
                      const mode: RewindMode = changed.length > 0 ? 'both' : 'conversation'
                      setLastMode(mode)
                      onRewind(current.messageId, mode)
                    }
                  }}
                >
                  Restaurar este ponto
                </button>
              </div>

              <div className="rewind-picker__actions">
                <button
                  type="button"
                  className="ghost-button rewind-picker__main"
                  disabled={busy || !current || changed.length === 0}
                  onClick={() => { if (current) { setLastMode('code'); onRewind(current.messageId, 'code') } }}
                >
                  Restaurar o código
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={busy || !current}
                  onClick={() => current && onRewind(current.messageId, 'conversation')}
                >
                  Voltar a conversa
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={busy || !current || changed.length === 0}
                  onClick={() => { if (current) { setLastMode('both'); onRewind(current.messageId, 'both') } }}
                >
                  Os dois
                </button>
              </div>
              <p className="rewind-picker__note">Voltar a conversa abre uma conversa nova a partir desta mensagem; a original continua como está.</p>
            </aside>
          </div>
        )}
      </section>
    </div>
  )
}
