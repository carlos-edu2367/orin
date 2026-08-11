import type { ExecutionState } from '../../api/types'

export type ExecutionStatus = ExecutionState

export type VisualStatus =
  | 'Preparando'
  | 'Trabalhando'
  | 'Aguardando ferramenta'
  | 'Aguardando você'
  | 'Pausado'
  | 'Concluído'
  | 'Falhou'
  | 'Cancelado'

export function toVisualStatus(state: ExecutionStatus): VisualStatus {
  switch (state) {
    case 'QUEUED':
    case 'STARTING':
      return 'Preparando'
    case 'RUNNING':
      return 'Trabalhando'
    case 'WAITING_TOOL':
      return 'Aguardando ferramenta'
    case 'WAITING_USER':
      return 'Aguardando você'
    case 'PAUSED':
      return 'Pausado'
    case 'COMPLETED':
      return 'Concluído'
    case 'FAILED':
      return 'Falhou'
    case 'CANCELLED':
      return 'Cancelado'
  }
}

export function isTerminalState(state: ExecutionStatus): boolean {
  return state === 'COMPLETED' || state === 'FAILED' || state === 'CANCELLED'
}
