import type { ExecutionControlAction } from '../../api/types'
import type { ExecutionStatus } from './executionStatus'
import { isTerminalState } from './executionStatus'

type ExecutionControlsProps = {
  state: ExecutionStatus
  pending?: boolean
  onAction?: (action: ExecutionControlAction) => void
}

export function ExecutionControls({ state, pending = false, onAction }: ExecutionControlsProps) {
  const terminal = isTerminalState(state)
  const canPause = state === 'QUEUED' || state === 'STARTING' || state === 'RUNNING' || state === 'WAITING_TOOL' || state === 'WAITING_USER'
  const canResume = state === 'PAUSED'

  return (
    <div className="execution-controls" aria-label="Controles da execução">
      {canResume ? (
        <button type="button" className="button button--secondary" disabled={terminal || pending} aria-label="Retomar execução" onClick={() => onAction?.('RESUME')}>
          <PlayIcon />
          Retomar
        </button>
      ) : (
        <button type="button" className="button button--secondary" disabled={!canPause || terminal || pending} aria-label="Pausar execução" onClick={() => onAction?.('PAUSE')}>
          <PauseIcon />
          Pausar
        </button>
      )}
      <button type="button" className="button button--quiet button--danger" disabled={terminal || pending} aria-label="Cancelar execução" onClick={() => onAction?.('CANCEL')}>
        <StopIcon />
        Cancelar
      </button>
    </div>
  )
}

function PauseIcon() {
  return <svg aria-hidden="true" viewBox="0 0 16 16" className="button__icon"><path d="M5 3.5v9M11 3.5v9" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" /></svg>
}

function PlayIcon() {
  return <svg aria-hidden="true" viewBox="0 0 16 16" className="button__icon"><path d="m6 4 5 4-5 4V4Z" fill="currentColor" /></svg>
}

function StopIcon() {
  return <svg aria-hidden="true" viewBox="0 0 16 16" className="button__icon"><rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor" /></svg>
}
