/** Small formatters shared by the pipeline screens. pt-BR, no emojis; a missing cost is never a zero. */
import type { PipelineSource } from '../../api/pipelines'

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('pt-BR')
}

/** `null` means the price was not known (a subscription without a price, for example). */
export function formatCost(value: number | null | undefined, known = true): string {
  if (value === null || value === undefined) return 'sem preço'
  const text = `US$ ${value.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 4 : 2 })}`
  return known ? text : `${text} (parcial)`
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined) return '—'
  const seconds = Math.round(milliseconds / 1000)
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ${String(seconds % 60).padStart(2, '0')} s`
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`
}

export function formatSeconds(seconds: number | null | undefined): string {
  return seconds === null || seconds === undefined ? '—' : formatDuration(seconds * 1000)
}

export const SOURCE_LABEL: Record<PipelineSource, string> = {
  builtin: 'Prontos',
  user: 'Seus pipelines',
  project: 'Do projeto',
}

export const RUN_STATE_LABEL: Record<string, string> = {
  running: 'Em execução',
  succeeded: 'Concluída',
  failed: 'Falhou',
  cancelled: 'Cancelada',
  interrupted: 'Interrompida',
  budget_exceeded: 'Orçamento esgotado',
}

export const STOP_REASON_LABEL: Record<string, string> = {
  paused: 'pausada pela pessoa',
  budget: 'o orçamento acabou',
  plan_mode: 'a conversa está no Modo Plano',
  cancelled: 'cancelada pela pessoa',
  input_invalid: 'uma entrada ficou inválida',
  engine_error: 'erro interno do motor',
  no_progress: 'sem progresso entre as voltas',
  loop_limit: 'o limite de voltas acabou',
  resume_conflict: 'conflito ao retomar',
  done: 'o avaliador considerou o objetivo cumprido',
  evaluator_failed: 'o avaliador falhou duas vezes seguidas',
  max_turns: 'o limite de turnos acabou',
  send_failed: 'não foi possível enviar o turno',
  cleared: 'limpo pela pessoa',
  superseded: 'substituído por um novo objetivo',
}

export function stopReasonText(reason: string | null): string | null {
  if (!reason) return null
  return STOP_REASON_LABEL[reason] ?? reason.replaceAll('_', ' ')
}

/** The visual weight of a state: a badge class suffix. State is always also written in words. */
export function toneOf(state: string): 'ok' | 'warn' | 'bad' | 'live' | 'muted' {
  if (state === 'succeeded' || state === 'reused') return 'ok'
  if (state === 'failed' || state === 'gate_failed' || state === 'cancelled') return 'bad'
  if (state === 'budget_exceeded' || state === 'interrupted' || state === 'stalled' || state === 'retrying') return 'warn'
  if (state === 'running') return 'live'
  return 'muted'
}
