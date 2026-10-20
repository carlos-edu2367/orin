import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { StructuredPatchHunk, ToolPermissionRequest } from './activityTypes'

export type DockToolPermissionRequest = ToolPermissionRequest & {
  target?: string
  reason?: string
  state?: 'pending' | 'allowed' | 'denied'
}

export type CodePermissionDockDecision = {
  behavior: 'allow' | 'deny'
  scope: 'once' | 'session' | 'project'
  updatedRule?: string
  reason?: string
  /** W7: "allow edits for the rest of this session" — the chat switches to accept-edits instead of saving a rule. */
  kind?: 'edits'
}

/** A file edit (no command): "don't ask again" means edits for this session, as in Claude Code. */
export function isEditRequest(request: DockToolPermissionRequest): boolean {
  const tool = (request.toolName || '').toLowerCase()
  return Boolean(request.path) && !request.command && /(edit|write|notebook|patch)/.test(tool)
}

function allowAgain(request: DockToolPermissionRequest): CodePermissionDockDecision {
  if (isEditRequest(request)) return { behavior: 'allow', scope: 'session', kind: 'edits' }
  return request.ruleSuggestion
    ? { behavior: 'allow', scope: 'project', updatedRule: request.ruleSuggestion }
    : { behavior: 'allow', scope: 'session' }
}

export type CodePermissionDockProps = {
  requests: DockToolPermissionRequest[]
  onResolve: (requestId: string, decision: CodePermissionDockDecision) => void
  busy?: boolean
}

const HIGH_RISK_COMMAND_PATTERNS = [
  /\b(?:cloudflared|ngrok|ssh|scp|rsync|curl\s+.*\|\s*(?:sh|bash)|wget\s+.*\|\s*(?:sh|bash))\b/i,
  /\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\b/i,
  /\bRemove-Item\b.*-Recurse/i,
  /\bdel\s+\/[sS]/i,
  /\bgit\s+(?:push\s+--force|reset\s+--hard|clean\s+-fd)\b/i,
  /(?:^|[\s"'/\\])\.env(?:\.|\b)/i,
]

export function assessPermissionRisk(request: DockToolPermissionRequest): 'high' | 'normal' {
  const haystack = [request.command, request.path, request.target, request.ruleSuggestion]
    .filter(Boolean)
    .join(' ')
  if (!haystack) return 'normal'
  for (const pattern of HIGH_RISK_COMMAND_PATTERNS) {
    if (pattern.test(haystack)) {
      return 'high'
    }
  }
  return 'normal'
}

function resolveBadgeAndTitle(request: DockToolPermissionRequest): {
  badge: string
  badgeVariant: 'bash' | 'edit' | 'git' | 'mcp' | 'plan'
  title: string
} {
  const tool = (request.toolName || '').toLowerCase()
  const cmd = (request.command || '').trim()

  if (tool === 'exit_plan_mode' || tool === 'enter_plan_mode' || request.planApproval) {
    return {
      badge: 'Plan',
      badgeVariant: 'plan',
      title: 'Aprovar plano de execução',
    }
  }
  if (tool.startsWith('mcp') || tool.includes('mcp')) {
    return {
      badge: 'MCP',
      badgeVariant: 'mcp',
      title: 'Executar ferramenta MCP',
    }
  }
  if (tool === 'git' || tool.startsWith('git_')) {
    return {
      badge: 'Git',
      badgeVariant: 'git',
      title: 'Executar operação Git',
    }
  }
  if (tool === 'run_command' || tool === 'bash') {
    if (/^git\b/i.test(cmd)) {
      return {
        badge: 'Git',
        badgeVariant: 'git',
        title: 'Executar comando no terminal',
      }
    }
    return {
      badge: 'Bash',
      badgeVariant: 'bash',
      title: 'Executar comando no terminal',
    }
  }
  if (tool === 'write_file') {
    return {
      badge: 'Write',
      badgeVariant: 'edit',
      title: 'Criar arquivo',
    }
  }
  if (tool === 'edit_file' || tool === 'notebook_edit') {
    return {
      badge: 'Edit',
      badgeVariant: 'edit',
      title: 'Editar arquivo',
    }
  }
  return {
    badge: request.toolName || 'Tool',
    badgeVariant: 'edit',
    title: `Permitir execução de ${request.toolName || 'ferramenta'}`,
  }
}

export function CodePermissionDock({
  requests,
  onResolve,
  busy = false,
}: CodePermissionDockProps) {
  const pendingRequests = requests.filter((req) => (req.state ?? 'pending') === 'pending')
  const current = pendingRequests[0] ?? null
  const queuedCount = Math.max(0, pendingRequests.length - 1)
  const currentRequestId = current?.requestId ?? null

  const [uiState, setUiState] = useState<{
    requestId: string | null
    feedbackOpen: boolean
    feedbackText: string
    diffExpanded: boolean
  }>({
    requestId: null,
    feedbackOpen: false,
    feedbackText: '',
    diffExpanded: false,
  })

  const feedbackOpen = uiState.requestId === currentRequestId ? uiState.feedbackOpen : false
  const feedbackText = uiState.requestId === currentRequestId ? uiState.feedbackText : ''
  const diffExpanded = uiState.requestId === currentRequestId ? uiState.diffExpanded : false

  const setFeedbackOpen = useCallback(
    (updater: boolean | ((prev: boolean) => boolean)) => {
      setUiState((prev) => {
        const baseOpen = prev.requestId === currentRequestId ? prev.feedbackOpen : false
        const nextOpen = typeof updater === 'function' ? updater(baseOpen) : updater
        return {
          requestId: currentRequestId,
          feedbackOpen: nextOpen,
          feedbackText: prev.requestId === currentRequestId ? prev.feedbackText : '',
          diffExpanded: prev.requestId === currentRequestId ? prev.diffExpanded : false,
        }
      })
    },
    [currentRequestId],
  )

  const setFeedbackText = useCallback(
    (nextText: string) => {
      setUiState((prev) => ({
        requestId: currentRequestId,
        feedbackOpen: prev.requestId === currentRequestId ? prev.feedbackOpen : false,
        feedbackText: nextText,
        diffExpanded: prev.requestId === currentRequestId ? prev.diffExpanded : false,
      }))
    },
    [currentRequestId],
  )

  const setDiffExpanded = useCallback(
    (updater: boolean | ((prev: boolean) => boolean)) => {
      setUiState((prev) => {
        const baseExpanded = prev.requestId === currentRequestId ? prev.diffExpanded : false
        const nextExpanded = typeof updater === 'function' ? updater(baseExpanded) : updater
        return {
          requestId: currentRequestId,
          feedbackOpen: prev.requestId === currentRequestId ? prev.feedbackOpen : false,
          feedbackText: prev.requestId === currentRequestId ? prev.feedbackText : '',
          diffExpanded: nextExpanded,
        }
      })
    },
    [currentRequestId],
  )

  const dockRef = useRef<HTMLDivElement | null>(null)
  const feedbackInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (feedbackOpen) {
      feedbackInputRef.current?.focus()
    }
  }, [feedbackOpen])

  useEffect(() => {
    if (!current || busy) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return

      const activeEl = document.activeElement as HTMLElement | null
      const isInsideDock = Boolean(activeEl && dockRef.current?.contains(activeEl))

      if (feedbackOpen && activeEl === feedbackInputRef.current) {
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          setFeedbackOpen(false)
        }
        return
      }

      if (event.key === 'Escape') {
        if (document.querySelector('[role="dialog"], [role="listbox"], [role="search"]')) return
        event.preventDefault()
        event.stopPropagation()
        onResolve(current.requestId, { behavior: 'deny', scope: 'once' })
        return
      }

      if (activeEl && !isInsideDock) {
        const tag = activeEl.tagName.toLowerCase()
        if (
          tag === 'input' ||
          tag === 'textarea' ||
          tag === 'select' ||
          activeEl.isContentEditable
        ) {
          return
        }
      }

      if (event.key === '1' || (event.key === 'Enter' && !feedbackOpen)) {
        event.preventDefault()
        event.stopPropagation()
        onResolve(current.requestId, { behavior: 'allow', scope: 'once' })
        return
      }

      if (event.key === '2') {
        event.preventDefault()
        event.stopPropagation()
        onResolve(current.requestId, allowAgain(current))
        return
      }

      if (event.key === '3') {
        event.preventDefault()
        event.stopPropagation()
        onResolve(current.requestId, { behavior: 'deny', scope: 'once' })
        return
      }

      if (event.key === 'n' || event.key === 'N') {
        event.preventDefault()
        event.stopPropagation()
        setFeedbackOpen(true)
      }
    }

    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [current, busy, feedbackOpen, onResolve, setFeedbackOpen])

  if (!current) {
    return null
  }

  const risk = assessPermissionRisk(current)
  const { badge, badgeVariant, title } = resolveBadgeAndTitle(current)
  const targetLabel = current.path ?? current.target ?? ''
  const commandPreview = current.command ?? (!current.path ? current.target : undefined)
  const hunks: StructuredPatchHunk[] = current.structuredPatch ?? []
  const hasDiff = hunks.length > 0

  const handleAllowOnce = () => {
    if (busy) return
    onResolve(current.requestId, { behavior: 'allow', scope: 'once' })
  }

  const handleAllowAlways = () => {
    if (busy) return
    onResolve(current.requestId, allowAgain(current))
  }
  const editRequest = isEditRequest(current)

  const handleDeny = () => {
    if (busy) return
    onResolve(current.requestId, { behavior: 'deny', scope: 'once' })
  }

  const handleSubmitFeedback = () => {
    if (busy) return
    const trimmed = feedbackText.trim()
    onResolve(current.requestId, {
      behavior: 'deny',
      scope: 'once',
      ...(trimmed ? { reason: trimmed } : {}),
    })
  }

  const dockClasses = [
    'code-perm-dock',
    queuedCount > 0 ? 'code-perm-dock--queued' : '',
    risk === 'high' ? 'code-perm-dock--high-risk' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="code-perm-dock-wrap" ref={dockRef}>
      <AnimatePresence mode="wait">
        <motion.div
          key={current.requestId}
          className={dockClasses}
          data-testid="code-permission-dock"
          role="region"
          aria-label={`Permissão requerida para ${current.toolName}`}
          initial={{ opacity: 0, y: 12, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 8, scale: 0.985 }}
          transition={{ type: 'spring', stiffness: 380, damping: 30, mass: 0.8 }}
        >
          <div className="code-perm-dock__head">
            <div className="code-perm-dock__title-row">
              <span
                className={`code-perm-dock__badge code-perm-dock__badge--${badgeVariant}`}
                data-testid="code-perm-dock-tool-badge"
              >
                {badge}
              </span>
              <span className="code-perm-dock__title">{title}</span>
              {targetLabel && targetLabel !== commandPreview && (
                <code className="code-perm-dock__target" data-testid="code-perm-dock-target">
                  {targetLabel}
                </code>
              )}
              {risk === 'high' && (
                <span
                  className="code-perm-dock__risk-badge"
                  data-testid="code-perm-dock-risk-badge"
                >
                  Alto risco
                </span>
              )}
            </div>
            {queuedCount > 0 && (
              <span
                className="code-perm-dock__queue-badge"
                data-testid="code-perm-dock-queue-badge"
              >
                Também aguardando: {queuedCount}
              </span>
            )}
          </div>

          {current.reason && (
            <p className="code-perm-dock__reason" data-testid="code-perm-dock-reason">
              {current.reason}
            </p>
          )}

          {commandPreview && (
            <pre className="code-perm-dock__cmd" data-testid="code-perm-dock-command">
              <code>$ {commandPreview}</code>
            </pre>
          )}

          {hasDiff && (
            <div className="code-perm-dock__diff" data-testid="code-perm-dock-diff">
              <div className="code-perm-dock__diff-head">
                <span className="code-perm-dock__diff-path">
                  {current.path ?? 'Alterações propostas'}
                </span>
                <div className="code-perm-dock__diff-stats">
                  {typeof current.addedLines === 'number' && (
                    <span className="text-emerald-300">+{current.addedLines}</span>
                  )}
                  {typeof current.removedLines === 'number' && (
                    <span className="text-rose-300">-{current.removedLines}</span>
                  )}
                  <button
                    type="button"
                    onClick={() => setDiffExpanded((prev) => !prev)}
                    className="code-perm-dock__diff-toggle"
                  >
                    {diffExpanded ? 'Recolher diff' : 'Ver diff completo'}
                  </button>
                </div>
              </div>
              <div className="code-perm-dock__diff-body">
                {hunks.slice(0, diffExpanded ? hunks.length : 1).map((hunk, hunkIdx) => (
                  <div key={`${hunk.oldStart}-${hunk.newStart}-${hunkIdx}`}>
                    {hunk.lines
                      .slice(0, diffExpanded ? hunk.lines.length : 8)
                      .map((line, lineIdx) => {
                        const isAdd = line.startsWith('+')
                        const isDel = line.startsWith('-')
                        return (
                          <div
                            key={lineIdx}
                            className={`code-perm-dock__diff-line ${
                              isAdd
                                ? 'code-perm-dock__diff-line--add'
                                : isDel
                                  ? 'code-perm-dock__diff-line--del'
                                  : ''
                            }`}
                          >
                            {line}
                          </div>
                        )
                      })}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="code-perm-dock__actions">
            <button
              type="button"
              disabled={busy}
              onClick={handleAllowOnce}
              data-testid="code-perm-allow-once"
              className="code-perm-dock__btn code-perm-dock__btn--primary"
            >
              <kbd className="code-perm-dock__kbd">1</kbd>
              <span>Sim</span>
            </button>

            <button
              type="button"
              disabled={busy}
              onClick={handleAllowAlways}
              data-testid="code-perm-allow-always"
              className="code-perm-dock__btn code-perm-dock__btn--secondary"
            >
              <kbd className="code-perm-dock__kbd">2</kbd>
              {editRequest ? (
                <span>Sim, e permitir edições nesta sessão</span>
              ) : current.ruleSuggestion ? (
                <>
                  <span>Sim, e não perguntar de novo para:</span>
                  <code className="code-perm-dock__rule">{current.ruleSuggestion}</code>
                  <span className="code-perm-dock__scope">(nesta pasta)</span>
                </>
              ) : (
                <span>Sim, e não perguntar de novo nesta sessão</span>
              )}
            </button>

            <button
              type="button"
              disabled={busy}
              onClick={handleDeny}
              data-testid="code-perm-deny"
              className="code-perm-dock__btn code-perm-dock__btn--danger"
            >
              <kbd className="code-perm-dock__kbd">3</kbd>
              <span>Não</span>
            </button>

            <button
              type="button"
              disabled={busy}
              onClick={() => setFeedbackOpen((prev) => !prev)}
              data-testid="code-perm-deny-instruct-toggle"
              className="code-perm-dock__btn code-perm-dock__btn--ghost"
            >
              <span>Não, e dizer o que fazer…</span>
            </button>
          </div>

          {feedbackOpen && (
            <div className="code-perm-dock__feedback" data-testid="code-perm-dock-feedback">
              <input
                ref={feedbackInputRef}
                type="text"
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handleSubmitFeedback()
                  } else if (e.key === 'Escape') {
                    e.preventDefault()
                    setFeedbackOpen(false)
                  }
                }}
                placeholder="Diga ao Orin como fazer diferente..."
                className="code-perm-dock__feedback-input"
              />
              <button
                type="button"
                disabled={busy}
                onClick={handleSubmitFeedback}
                className="code-perm-dock__btn code-perm-dock__btn--primary"
              >
                Enviar orientação
              </button>
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  )
}
