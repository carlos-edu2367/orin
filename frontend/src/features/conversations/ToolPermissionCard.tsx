import { useState } from 'react'
import { StructuredDiffView } from './WorkspaceFileCard'
import type { ToolPermissionDecision, ToolPermissionRequest } from './activityTypes'

export type ToolPermissionCardProps = {
  request: ToolPermissionRequest
  active?: boolean
  onDecision?: (decision: ToolPermissionDecision) => Promise<void>
}

export function ToolPermissionCard({ request, active = true, onDecision }: ToolPermissionCardProps) {
  const [busy, setBusy] = useState<ToolPermissionDecision | null>(null)
  const [resolved, setResolved] = useState<ToolPermissionDecision | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canAct = active && resolved === null && Boolean(onDecision)
  const hasPatch = Boolean(request.structuredPatch && request.structuredPatch.length > 0)
  const hasStats = request.addedLines !== undefined || request.removedLines !== undefined

  async function decide(decision: ToolPermissionDecision) {
    if (!canAct || busy || !onDecision) return
    setBusy(decision)
    setError(null)
    try {
      await onDecision(decision)
      setResolved(decision)
    } catch {
      setError('Não foi possível registrar esta permissão. Tente novamente.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section
      className="approval-card tool-permission-card"
      data-testid="tool-permission-card"
      data-active={canAct}
      data-resolved={resolved ?? undefined}
      aria-label={`Permissão para ${request.toolName}`}
    >
      <header className="approval-card__header">
        <span className="approval-card__glyph" aria-hidden="true">›_</span>
        <div>
          <strong>Permissão necessária · {request.toolName}</strong>
          <p>
            {request.path
              ? `Arquivo: ${request.path}`
              : request.command
                ? `Comando no terminal`
                : `Execução de ${request.toolName}`}
            {request.ruleSuggestion ? ` · Regra: ${request.ruleSuggestion}` : ''}
          </p>
        </div>
        {hasStats && (
          <span className="tool-permission-card__stats" aria-label={`Diff +${request.addedLines ?? 0} -${request.removedLines ?? 0}`}>
            <span className="workspace-diff__stat-add">+{request.addedLines ?? 0}</span>
            {' '}
            <span className="workspace-diff__stat-del">-{request.removedLines ?? 0}</span>
          </span>
        )}
      </header>

      <div className="approval-card__form">
        {request.command && (
          <pre className="tool-permission-card__command">
            <code>$ {request.command}</code>
          </pre>
        )}

        {hasPatch && request.structuredPatch && (
          <div className="tool-permission-card__diff">
            <StructuredDiffView hunks={request.structuredPatch} mode="unified" />
          </div>
        )}

        {resolved && (
          <p className="approval-card__hint" role="status">
            {resolved === 'allow_once' && 'Permissão concedida para esta ação.'}
            {resolved === 'allow_always' && `Sempre permitido neste projeto${request.ruleSuggestion ? ` (${request.ruleSuggestion})` : ''}.`}
            {resolved === 'deny' && 'Ação negada.'}
          </p>
        )}

        {canAct && (
          <footer className="approval-card__actions">
            <button
              type="button"
              className="approval-card__decline"
              onClick={() => void decide('deny')}
              disabled={busy !== null}
            >
              {busy === 'deny' ? 'Negando…' : 'Negar'}
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => void decide('allow_always')}
              disabled={busy !== null}
            >
              {busy === 'allow_always' ? 'Salvando…' : 'Sempre permitir no projeto'}
            </button>
            <button
              type="button"
              className="approval-card__submit"
              onClick={() => void decide('allow_once')}
              disabled={busy !== null}
            >
              {busy === 'allow_once' ? 'Permitindo…' : 'Permitir'}
            </button>
          </footer>
        )}

        {error && <p className="approval-card__error" role="alert">{error}</p>}
      </div>
    </section>
  )
}
