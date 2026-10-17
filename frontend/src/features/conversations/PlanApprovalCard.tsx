import { useState } from 'react'
import { MarkdownMessage } from './MarkdownMessage'

export type PlanApprovalDecision = 'accept_edits' | 'ask_permissions' | 'changes_requested'

export type PlanApprovalCardProps = {
  planPath?: string | null
  planContent?: string | null
  onApproveAcceptEdits: () => void
  onApproveAskPermissions: () => void
  onRequestChanges?: (feedback: string) => void
  disabled?: boolean
  resolvedDecision?: PlanApprovalDecision | null
}

function formatReceiptLabel(decision: PlanApprovalDecision | null, disabled: boolean): string | null {
  if (decision === 'accept_edits') return '✓ Plano aprovado (aceitar edições)'
  if (decision === 'ask_permissions') return '✓ Plano aprovado (revisão manual)'
  if (decision === 'changes_requested') return '✓ Ajustes solicitados'
  if (disabled) return 'Plano encerrado'
  return null
}

export function PlanApprovalCard({
  planPath,
  planContent,
  onApproveAcceptEdits,
  onApproveAskPermissions,
  onRequestChanges,
  disabled = false,
  resolvedDecision = null,
}: PlanApprovalCardProps) {
  const [editingFeedback, setEditingFeedback] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [submittedDecision, setSubmittedDecision] = useState<PlanApprovalDecision | null>(null)

  const effectiveDecision = submittedDecision ?? resolvedDecision ?? null
  const isLocked = disabled || Boolean(effectiveDecision)
  const receiptLabel = formatReceiptLabel(effectiveDecision, disabled)

  const handleApproveAcceptEdits = () => {
    if (isLocked) return
    setSubmittedDecision('accept_edits')
    setEditingFeedback(false)
    onApproveAcceptEdits()
  }

  const handleApproveAskPermissions = () => {
    if (isLocked) return
    setSubmittedDecision('ask_permissions')
    setEditingFeedback(false)
    onApproveAskPermissions()
  }

  const handleFeedbackSubmit = () => {
    const trimmed = feedback.trim()
    if (!trimmed || isLocked) return
    setSubmittedDecision('changes_requested')
    onRequestChanges?.(trimmed)
    setFeedback('')
    setEditingFeedback(false)
  }

  return (
    <section
      className="plan-approval-card my-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 text-[var(--text-primary)] shadow-sm"
      data-testid="plan-approval-card"
      data-resolved={isLocked ? 'true' : 'false'}
      aria-label="Plano pronto para revisão"
    >
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-subtle)] pb-2.5">
        <div className="flex items-center gap-2">
          <span
            className="inline-flex h-5 w-5 items-center justify-center rounded bg-[var(--accent-primary)]/15 font-mono text-[11px] font-bold text-[var(--accent-primary)]"
            aria-hidden="true"
          >
            {isLocked ? '✓' : '⏸'}
          </span>
          <strong className="text-[14px] font-semibold text-[var(--text-primary)]">
            Plano pronto para revisão
          </strong>
        </div>
        <div className="flex items-center gap-2">
          {receiptLabel && (
            <span
              className="rounded-md border border-emerald-500/30 bg-emerald-500/15 px-2 py-0.5 font-mono text-[11px] font-medium text-emerald-300"
              data-testid="plan-approval-receipt"
            >
              {receiptLabel}
            </span>
          )}
          {planPath && (
            <span
              className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-subtle)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-secondary)]"
              data-testid="plan-approval-path"
            >
              {planPath}
            </span>
          )}
        </div>
      </header>

      <div
        className="plan-approval-card__body mb-4 max-h-[420px] overflow-y-auto rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-subtle)] p-3 text-[13px]"
        data-testid="plan-approval-content"
      >
        {planContent && planContent.trim() ? (
          <MarkdownMessage content={planContent} />
        ) : (
          <p className="font-mono text-[12px] text-[var(--text-muted)]">
            {planPath ? `Plano salvo em ${planPath}` : 'Plano aguardando aprovação.'}
          </p>
        )}
      </div>

      <footer className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={isLocked}
            onClick={handleApproveAcceptEdits}
            data-testid="plan-approve-accept-edits"
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-emerald-500 disabled:opacity-50 transition-colors"
          >
            Sim, aprovar e aceitar edições automaticamente (Shift+Tab)
          </button>

          <button
            type="button"
            disabled={isLocked}
            onClick={handleApproveAskPermissions}
            data-testid="plan-approve-ask-permissions"
            className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-hover)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-primary)] hover:border-[var(--accent-primary)] disabled:opacity-50 transition-colors"
          >
            Sim, aprovar e revisar cada edição manualmente
          </button>

          {onRequestChanges && (
            <button
              type="button"
              disabled={isLocked}
              onClick={() => setEditingFeedback((prev) => !prev)}
              data-testid="plan-request-changes-toggle"
              className="rounded-lg border border-transparent px-2.5 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] disabled:opacity-50 transition-colors"
            >
              Pedir ajustes no plano...
            </button>
          )}
        </div>

        {editingFeedback && onRequestChanges && !isLocked && (
          <div className="mt-1 flex items-center gap-2">
            <input
              type="text"
              value={feedback}
              disabled={isLocked}
              onChange={(e) => setFeedback(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  handleFeedbackSubmit()
                }
              }}
              placeholder="Descreva os ajustes desejados no plano..."
              aria-label="Ajustes no plano"
              className="flex-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-subtle)] px-3 py-1.5 text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent-primary)] focus:outline-none"
            />
            <button
              type="button"
              disabled={isLocked || !feedback.trim()}
              onClick={handleFeedbackSubmit}
              data-testid="plan-request-changes-submit"
              className="rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-50 transition-opacity"
            >
              Enviar ajustes
            </button>
          </div>
        )}
      </footer>
    </section>
  )
}
