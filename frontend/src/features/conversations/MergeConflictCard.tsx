import { useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { discardAgentWorktree, type DiscardAgentWorktreeResponse } from '../../api/conversations'
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import './MergeConflictCard.css'

export type MergeResolveDetails = {
  agentId: string
  files: string[]
  worktreePath: string | null
  kind: 'conflict' | 'check_failed'
}

export type MergeConflictCardProps = {
  group: ActivityGroup
  conversationId?: string
  client?: ApiClient
  onOpenWorktree?: (worktreePath: string, agentId: string) => void
  onAskAgentToResolve?: (prompt: string, details: MergeResolveDetails) => void | Promise<void>
  onDiscardWorktree?: (conversationId: string, agentId: string) => Promise<DiscardAgentWorktreeResponse>
}

type MergeKind = 'applied' | 'conflict' | 'check_failed'

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function formatMergeDetail(raw: unknown): string | null {
  const plain = asString(raw)
  if (plain) return plain
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const obj = raw as Record<string, unknown>
  const parts: string[] = []
  const command = asString(obj.command)
  if (command) {
    const exitCode = typeof obj.exit_code === 'number' && Number.isFinite(obj.exit_code)
      ? ` (exit code ${obj.exit_code})`
      : ''
    parts.push(`$ ${command}${exitCode}`)
  }
  const message = asString(obj.message)
  if (message) parts.push(message)
  const hunks = asRecord(obj.hunks)
  const hunkEntries = Object.entries(hunks)
    .map(([file, snippet]) => {
      const text = asString(snippet)
      return text ? `--- ${file} ---\n${text}` : null
    })
    .filter((entry): entry is string => entry !== null)
  if (hunkEntries.length > 0) {
    parts.push(hunkEntries.join('\n\n'))
  }
  const checkOutput = asString(obj.check_output)
  if (checkOutput) parts.push(checkOutput)
  const stderr = asString(obj.stderr)
  if (stderr) parts.push(stderr)
  if (parts.length === 0) {
    const reason = asString(obj.reason)
    if (reason) parts.push(reason)
  }
  return parts.length > 0 ? parts.join('\n\n') : null
}

function resolveMergeKind(event: ConversationActivityEvent): MergeKind {
  if (event.type === 'merge.applied') return 'applied'
  if (event.type === 'merge.check_failed') return 'check_failed'
  return 'conflict'
}

function buildResolvePrompt(details: MergeResolveDetails): string {
  const fileList = details.files.length > 0 ? details.files.join(', ') : 'os arquivos alterados'
  const pathPart = details.worktreePath ? ` na worktree \`${details.worktreePath}\`` : ''
  if (details.kind === 'check_failed') {
    return `A checagem de integração da worktree do subagente \`${details.agentId}\` falhou${pathPart} nos arquivos: ${fileList}. Inspecione as alterações da worktree, corrija o problema e integre com segurança.`
  }
  return `Houve conflito ao integrar a worktree do subagente \`${details.agentId}\`${pathPart} nos arquivos: ${fileList}. Inspecione os conflitos a partir dessa worktree e resolva-os no workspace principal.`
}

const TITLE_BY_KIND: Record<MergeKind | 'discarded', string> = {
  applied: 'Alterações da worktree integradas',
  conflict: 'Conflito ao integrar worktree',
  check_failed: 'Checagem de integração falhou',
  discarded: 'Worktree descartada',
}

const BADGE_BY_KIND: Record<MergeKind | 'discarded', string> = {
  applied: 'Integrado',
  conflict: 'Conflito',
  check_failed: 'Checagem falhou',
  discarded: 'Descartado',
}

export function MergeConflictCard({
  group,
  conversationId,
  client,
  onOpenWorktree,
  onAskAgentToResolve,
  onDiscardWorktree,
}: MergeConflictCardProps) {
  const latest = group.events[group.events.length - 1] ?? group.events[0]
  const payload = asRecord(latest?.payload)
  const rawKind = latest ? resolveMergeKind(latest) : 'conflict'
  const agentId = asString(payload.agent_id) ?? latest?.agentId ?? group.agentId
  const files = asStringList(payload.files)
  const worktreePath = asString(payload.worktree_path)
  const branch = asString(payload.branch)
  const detail = formatMergeDetail(payload.conflict_detail) ?? formatMergeDetail(payload.detail)

  const [discardState, setDiscardState] = useState<'idle' | 'working' | 'discarded' | 'failed'>('idle')
  const [statusNotice, setStatusNotice] = useState<string | null>(null)
  const [errorNotice, setErrorNotice] = useState<string | null>(null)
  const [resolving, setResolving] = useState(false)

  const effectiveKind: MergeKind | 'discarded' = discardState === 'discarded' ? 'discarded' : rawKind
  const needsResolution = effectiveKind === 'conflict' || effectiveKind === 'check_failed'

  function handleOpenWorktree() {
    if (!worktreePath) return
    setErrorNotice(null)
    if (onOpenWorktree) {
      onOpenWorktree(worktreePath, agentId)
      return
    }
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      void navigator.clipboard.writeText(worktreePath).then(
        () => setStatusNotice('Caminho da worktree copiado.'),
        () => setStatusNotice(`Worktree disponível em: ${worktreePath}`),
      )
    } else {
      setStatusNotice(`Worktree disponível em: ${worktreePath}`)
    }
  }

  async function handleDiscard() {
    if (!agentId || discardState === 'working' || discardState === 'discarded') return
    if (!conversationId && !onDiscardWorktree) return
    setDiscardState('working')
    setErrorNotice(null)
    setStatusNotice(null)
    try {
      if (onDiscardWorktree) {
        await onDiscardWorktree(conversationId ?? '', agentId)
      } else {
        await discardAgentWorktree(client ?? createBrowserApiClient(), conversationId as string, agentId)
      }
      setDiscardState('discarded')
      setStatusNotice('Worktree descartada com segurança.')
    } catch {
      setDiscardState('failed')
      setErrorNotice('Não foi possível descartar a worktree.')
    }
  }

  async function handleAskAgent() {
    if (resolving || !needsResolution) return
    setResolving(true)
    setErrorNotice(null)
    const details: MergeResolveDetails = {
      agentId,
      files,
      worktreePath,
      kind: effectiveKind === 'check_failed' ? 'check_failed' : 'conflict',
    }
    const prompt = buildResolvePrompt(details)
    try {
      await onAskAgentToResolve?.(prompt, details)
      setStatusNotice('Solicitação enviada ao agente.')
    } catch {
      setErrorNotice('Não foi possível enviar a solicitação ao agente.')
    } finally {
      setResolving(false)
    }
  }

  return (
    <article
      className="merge-card"
      data-testid="merge-conflict-card"
      data-merge-kind={effectiveKind}
      aria-label={TITLE_BY_KIND[effectiveKind]}
    >
      <header className="merge-card__header">
        <div className="merge-card__title-group">
          <h4 className="merge-card__title">{TITLE_BY_KIND[effectiveKind]}</h4>
          <span className="merge-card__meta-line">
            {group.agentName ? `${group.agentName} (${agentId})` : agentId}
            {branch ? ` · ${branch}` : ''}
          </span>
        </div>
        <span className={`merge-card__badge merge-card__badge--${effectiveKind}`}>
          {BADGE_BY_KIND[effectiveKind]}
        </span>
      </header>

      {worktreePath && (
        <p className="merge-card__path">
          <span>Worktree:</span> <code>{worktreePath}</code>
        </p>
      )}

      {files.length > 0 && (
        <section className="merge-card__section" aria-label="Arquivos afetados">
          <h5 className="merge-card__section-title">
            {effectiveKind === 'applied' ? 'Arquivos integrados' : 'Arquivos em conflito'} ({files.length})
          </h5>
          <ul className="merge-card__files" data-testid="merge-conflict-files">
            {files.map((file) => (
              <li key={file} className="merge-card__file">
                <code>{file}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

      {detail && (
        <pre className="merge-card__detail" data-testid="merge-conflict-detail">
          {detail}
        </pre>
      )}

      {needsResolution && (
        <div className="merge-card__actions">
          <button
            type="button"
            className="ghost-button"
            disabled={!worktreePath}
            onClick={handleOpenWorktree}
          >
            Abrir worktree
          </button>
          <button
            type="button"
            className="ghost-button merge-card__action--danger"
            disabled={discardState === 'working' || (!conversationId && !onDiscardWorktree)}
            onClick={() => void handleDiscard()}
          >
            {discardState === 'working' ? 'Descartando…' : 'Descartar'}
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={resolving || !onAskAgentToResolve}
            onClick={() => void handleAskAgent()}
          >
            {resolving ? 'Enviando…' : 'Pedir ao agente para resolver'}
          </button>
        </div>
      )}

      {statusNotice && (
        <p className="merge-card__notice" role="status">
          {statusNotice}
        </p>
      )}
      {errorNotice && (
        <p className="merge-card__error" role="alert">
          {errorNotice}
        </p>
      )}
    </article>
  )
}
