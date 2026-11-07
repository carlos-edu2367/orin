import { useState } from 'react'
import { StructuredDiffView } from './WorkspaceFileCard'
import type { ToolPermissionDecision, ToolPermissionRequest } from './activityTypes'
import './toolPermissionCard.css'

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
  // A production action (deploy, publish, DB write, push) is approved for this command only.
  const sensitive = request.sensitive && request.sensitive.labels.length > 0 ? request.sensitive : null

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
      className={sensitive ? 'approval-card tool-permission-card tool-permission-card--sensitive' : 'approval-card tool-permission-card'}
      data-testid="tool-permission-card"
      data-active={canAct}
      data-sensitive={sensitive ? 'true' : undefined}
      data-resolved={resolved ?? undefined}
      aria-label={sensitive ? 'Aprovação de ação em produção' : `Permissão para ${request.toolName}`}
    >
      <header className="approval-card__header">
        <span className="approval-card__glyph" aria-hidden="true">{sensitive ? '!' : '›_'}</span>
        <div>
          <strong>{sensitive ? 'Aprovação necessária · ação em produção' : `Permissão necessária · ${request.toolName}`}</strong>
          <p>
            {sensitive
              ? 'Este comando só roda se você aprovar. A aprovação vale apenas para este comando.'
              : request.path
                ? `Arquivo: ${request.path}`
                : request.command
                  ? `Comando no terminal`
                  : `Execução de ${request.toolName}`}
            {!sensitive && request.ruleSuggestion ? ` · Regra: ${request.ruleSuggestion}` : ''}
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
        {sensitive && (
          <ul className="tool-permission-card__effects" data-testid="tool-permission-effects">
            {sensitive.labels.map((label) => <li key={label}>{label}</li>)}
          </ul>
        )}

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
            {resolved === 'allow_once' && (sensitive ? 'Aprovado. O comando vai rodar agora.' : 'Permissão concedida para esta ação.')}
            {resolved === 'allow_always' && `Sempre permitido neste projeto${request.ruleSuggestion ? ` (${request.ruleSuggestion})` : ''}.`}
            {resolved === 'deny' && (sensitive ? 'Negado. Nada foi executado.' : 'Ação negada.')}
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
            {!sensitive && (
              <button
                type="button"
                className="ghost-button"
                onClick={() => void decide('allow_always')}
                disabled={busy !== null}
              >
                {busy === 'allow_always' ? 'Salvando…' : 'Sempre permitir no projeto'}
              </button>
            )}
            <button
              type="button"
              className="approval-card__submit"
              onClick={() => void decide('allow_once')}
              disabled={busy !== null}
            >
              {busy === 'allow_once'
                ? (sensitive ? 'Aprovando…' : 'Permitindo…')
                : (sensitive ? 'Aprovar e executar' : 'Permitir')}
            </button>
          </footer>
        )}

        {error && <p className="approval-card__error" role="alert">{error}</p>}
      </div>
    </section>
  )
}
