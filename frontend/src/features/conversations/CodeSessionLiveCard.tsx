export type CodeSessionLiveCardProps = {
  codeConversationId: string
  goal: string
  status: 'running' | 'completed' | 'failed'
  completedTodoCount: number
  totalTodoCount: number
  activeTaskTitle?: string | null
  modifiedFilesCount?: number
  examinedFilesCount?: number
  friendlyMilestone?: string | null
  onOpenCodeSession: (codeConversationId: string) => void
}

function defaultFriendlyMilestone(
  status: 'running' | 'completed' | 'failed',
  completed: number,
  total: number,
): string {
  if (status === 'completed') return 'Tudo pronto no Orin Code!'
  if (status === 'failed') return 'Precisa de atenção no Orin Code'
  if (total <= 0) return 'Iniciando trabalho no código…'
  const ratio = completed / total
  if (ratio >= 0.75) return `${completed}/${total} concluídas — Reta final!`
  if (ratio >= 0.4) return `${completed}/${total} concluídas — Na metade do caminho`
  return `${completed}/${total} concluídas — Avançando nas etapas`
}

export function CodeSessionLiveCard({
  codeConversationId,
  goal,
  status,
  completedTodoCount,
  totalTodoCount,
  activeTaskTitle,
  modifiedFilesCount = 0,
  examinedFilesCount = 0,
  friendlyMilestone,
  onOpenCodeSession,
}: CodeSessionLiveCardProps) {
  const milestoneText =
    friendlyMilestone ?? defaultFriendlyMilestone(status, completedTodoCount, totalTodoCount)
  const percent =
    totalTodoCount > 0
      ? Math.min(100, Math.round((completedTodoCount / totalTodoCount) * 100))
      : status === 'completed'
        ? 100
        : 25

  const statusLabel =
    status === 'completed' ? 'Concluído' : status === 'failed' ? 'Atenção' : 'Em andamento'

  return (
    <div
      className="code-session-live-card"
      data-testid="code-session-live-card"
      data-status={status}
      style={{
        marginTop: '0.65rem',
        padding: '0.9rem 1rem',
        borderRadius: '0.85rem',
        background: 'rgba(16, 14, 32, 0.92)',
        border: '1px solid rgba(139, 92, 246, 0.32)',
        display: 'grid',
        gap: '0.55rem',
        color: '#F4F2FF',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span
            aria-hidden="true"
            style={{
              display: 'inline-block',
              width: '8px',
              height: '8px',
              borderRadius: '999px',
              background:
                status === 'completed' ? '#34D399' : status === 'failed' ? '#F87171' : '#8B5CF6',
            }}
          />
          <strong style={{ fontSize: '0.86rem', color: '#DDD6FE' }}>Orin Code em segundo plano</strong>
        </div>
        <span
          style={{
            fontSize: '0.74rem',
            padding: '0.15rem 0.55rem',
            borderRadius: '999px',
            background: 'rgba(139, 92, 246, 0.18)',
            color: '#C4B5FD',
            fontWeight: 600,
          }}
        >
          {statusLabel}
        </span>
      </div>

      <p style={{ margin: 0, fontSize: '0.9rem', fontWeight: 500 }}>{goal}</p>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.8rem' }}>
        <span data-testid="code-session-live-milestone" style={{ color: '#A78BFA', fontWeight: 600 }}>
          {milestoneText}
        </span>
        {(modifiedFilesCount > 0 || examinedFilesCount > 0) && (
          <span style={{ opacity: 0.76, fontFamily: 'var(--font-mono, monospace)', fontSize: '0.75rem' }}>
            {modifiedFilesCount} editados · {examinedFilesCount} lidos
          </span>
        )}
      </div>

      <div
        style={{
          height: '6px',
          borderRadius: '999px',
          background: 'rgba(255, 255, 255, 0.08)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: '100%',
            background:
              status === 'completed'
                ? '#34D399'
                : status === 'failed'
                  ? '#F87171'
                  : 'linear-gradient(90deg, #8B5CF6, #A78BFA)',
            transition: 'width 220ms ease',
          }}
        />
      </div>

      {activeTaskTitle && (
        <div style={{ fontSize: '0.8rem', opacity: 0.85 }}>
          Agora: <strong>{activeTaskTitle}</strong>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '0.15rem' }}>
        <button
          type="button"
          className="btn-secondary"
          data-testid="open-in-orin-code-button"
          onClick={() => onOpenCodeSession(codeConversationId)}
          style={{ fontSize: '0.8rem', padding: '0.32rem 0.75rem' }}
        >
          Abrir no Orin Code →
        </button>
      </div>
    </div>
  )
}
