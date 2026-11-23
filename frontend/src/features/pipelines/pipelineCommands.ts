/**
 * `/goal`, `/pipeline` and `/pipelines` (0.13 WS-C11). Parsing is pure and tested apart from the handlers.
 * None of these sends anything to the model: `/goal` starts the objective through the API, and the other two navigate.
 */
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { clearGoal, getGoal, resumeGoal, startGoal, type GoalStatus } from '../../api/pipelines'
import { formatTokens, stopReasonText } from './format'

export const GOAL_MIN_TOKENS = 1_000
export const GOAL_MAX_TOKENS = 5_000_000

export const GOAL_USAGE = 'Uso: /goal <objetivo> [--max-tokens N] · /goal status · /goal clear · /goal resume [--max-tokens N]'
export const PIPELINE_USAGE = 'Uso: /pipeline <id> [chave=valor ...]. Valores com espaço vão entre aspas. Veja os ids em /pipelines.'

export type GoalCommand =
  | { kind: 'start'; objective: string; maxTokens: number | null }
  | { kind: 'status' }
  | { kind: 'clear' }
  | { kind: 'resume'; maxTokens: number | null }
  | { kind: 'error'; message: string }

function readMaxTokens(args: string): { rest: string; maxTokens: number | null; error: string | null } {
  const match = /(?:^|\s)--max-tokens(?:=|\s+)(\S+)/.exec(args)
  if (!match) {
    return /(?:^|\s)--max-tokens\s*$/.test(args)
      ? { rest: args, maxTokens: null, error: '--max-tokens precisa de um número. ' + GOAL_USAGE }
      : { rest: args.trim(), maxTokens: null, error: null }
  }
  const raw = match[1].replace(/[._,]/g, '')
  const value = /^\d+$/.test(raw) ? Number(raw) : Number.NaN
  const rest = (args.slice(0, match.index) + ' ' + args.slice(match.index + match[0].length)).replace(/\s+/g, ' ').trim()
  if (!Number.isFinite(value) || value < GOAL_MIN_TOKENS || value > GOAL_MAX_TOKENS) {
    return { rest, maxTokens: null, error: `--max-tokens deve ficar entre ${formatTokens(GOAL_MIN_TOKENS)} e ${formatTokens(GOAL_MAX_TOKENS)}.` }
  }
  return { rest, maxTokens: value, error: null }
}

export function parseGoalArgs(args: string): GoalCommand {
  const clean = args.trim()
  if (!clean) return { kind: 'error', message: GOAL_USAGE }
  const { rest, maxTokens, error } = readMaxTokens(clean)
  if (error) return { kind: 'error', message: error }
  const word = rest.toLowerCase()
  if (word === 'status') return { kind: 'status' }
  if (word === 'clear') return { kind: 'clear' }
  if (word === 'resume') return { kind: 'resume', maxTokens }
  if (!rest) return { kind: 'error', message: GOAL_USAGE }
  return { kind: 'start', objective: rest, maxTokens }
}

export type PipelineCommand =
  | { kind: 'open'; pipelineId: string; inputs: Record<string, string> }
  | { kind: 'error'; message: string }

/** Splits on spaces but keeps "quoted text" together. */
function words(text: string): string[] {
  const out: string[] = []
  const pattern = /(?:[^\s"']+|"[^"]*"|'[^']*')+/g
  for (const match of text.matchAll(pattern)) out.push(match[0])
  return out
}

function unquote(value: string): string {
  return value.replace(/^(["'])(.*)\1$/s, '$2')
}

export function parsePipelineArgs(args: string): PipelineCommand {
  const parts = words(args.trim())
  if (parts.length === 0) return { kind: 'error', message: PIPELINE_USAGE }
  const pipelineId = parts[0].toLowerCase()
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(pipelineId)) {
    return { kind: 'error', message: `"${parts[0]}" não é um id de pipeline válido (letras minúsculas, números e "-"). ${PIPELINE_USAGE}` }
  }
  const inputs: Record<string, string> = {}
  for (const part of parts.slice(1)) {
    const split = part.indexOf('=')
    if (split <= 0) return { kind: 'error', message: `"${part}" não tem a forma chave=valor. ${PIPELINE_USAGE}` }
    const key = part.slice(0, split)
    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(key)) return { kind: 'error', message: `"${key}" não é um nome de entrada válido.` }
    inputs[key] = unquote(part.slice(split + 1))
  }
  return { kind: 'open', pipelineId, inputs }
}

/** The address that opens the panel with a run form already filled in. */
export function pipelineFormPath(conversationId: string | null, pipelineId: string, inputs: Record<string, string> = {}): string {
  const query = new URLSearchParams()
  if (conversationId) query.set('conversation', conversationId)
  query.set('pipeline', pipelineId)
  query.set('run', '1')
  for (const [key, value] of Object.entries(inputs)) query.set(`in.${key}`, value)
  return `/pipelines?${query.toString()}`
}

const GOAL_ERRORS: Record<string, string> = {
  GOAL_ACTIVE: 'Já existe um objetivo ativo nesta conversa. Use /goal clear para encerrá-lo antes de começar outro.',
  GOAL_NOT_FOUND: 'Não há objetivo ativo ou parado nesta conversa.',
  GOAL_NOT_RESUMABLE: 'Este objetivo não pode ser retomado (já terminou ou foi limpo). Comece outro com /goal <objetivo>.',
  GOAL_OBJECTIVE_INVALID: 'Escreva o objetivo (até 4.000 caracteres).',
  GOAL_MAX_TOKENS_INVALID: `--max-tokens deve ficar entre ${formatTokens(GOAL_MIN_TOKENS)} e ${formatTokens(GOAL_MAX_TOKENS)}.`,
}

export function goalErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (GOAL_ERRORS[error.code]) return GOAL_ERRORS[error.code]
    if (error.status === 429) return 'Muitos pedidos seguidos. Espere um instante e tente de novo.'
    if (error.status === 404) return 'O /goal não está disponível neste Orin.'
    if (error.serverMessage) return error.serverMessage
  }
  return 'Não foi possível executar o /goal. Tente de novo.'
}

export function goalSummaryLines(goal: GoalStatus): string[] {
  const lines = [
    `Objetivo: ${goal.objective}`,
    `Estado: ${goal.state === 'running' ? 'em andamento' : goal.state === 'succeeded' ? 'cumprido' : goal.state === 'interrupted' ? 'interrompido' : goal.state === 'budget_exceeded' ? 'teto de tokens atingido' : goal.state === 'cancelled' ? 'limpo' : 'parado'}${goal.stopReason ? ` (${stopReasonText(goal.stopReason)})` : ''}`,
    `Tokens: ${formatTokens(goal.windowUsedTokens)} de ${formatTokens(goal.maxTokens)} (restam ${formatTokens(goal.remainingTokens)}) · turnos: ${goal.turns}`,
  ]
  if (goal.lastEvaluation?.reason) {
    lines.push(`Última avaliação${goal.evaluatorModel ? ` (${goal.evaluatorModel}${goal.lastEvaluation.fallback ? ', modelo do turno em esforço baixo' : ''})` : ''}: ${goal.lastEvaluation.reason}`)
  }
  return lines
}

export type PipelineCommandDeps = {
  client: ApiClient
  conversationId?: string
  navigate: (path: string) => void
  notify: (text: string) => void
  openInfo: (panel: { kind: 'text'; title: string; lines: string[] }) => void
}

type Handler = (args: string) => Promise<void> | void

export function pipelineCommandHandlers(deps: PipelineCommandDeps): Record<string, Handler> {
  const conversationId = deps.conversationId ?? ''
  const needConversation = (): boolean => {
    if (conversationId) return true
    deps.notify('Abra uma conversa para usar o /goal.')
    return false
  }
  return {
    goal: async (args) => {
      const command = parseGoalArgs(args)
      if (command.kind === 'error') { deps.notify(command.message); return }
      if (!needConversation()) return
      try {
        if (command.kind === 'start') {
          const started = await startGoal(deps.client, conversationId, command.objective, command.maxTokens)
          deps.notify(`Objetivo iniciado. Teto de ${formatTokens(started.maxTokens)} tokens; o Orin continua em turnos até um avaliador pequeno dizer que terminou.`)
        } else if (command.kind === 'status') {
          const goal = await getGoal(deps.client, conversationId)
          if (!goal) { deps.notify('Não há objetivo nesta conversa. Comece um com /goal <objetivo>.'); return }
          deps.openInfo({ kind: 'text', title: 'Objetivo (/goal)', lines: goalSummaryLines(goal) })
        } else if (command.kind === 'clear') {
          await clearGoal(deps.client, conversationId, false)
          deps.notify('Objetivo encerrado. O turno em andamento, se houver, termina normalmente e não é avaliado.')
        } else {
          await resumeGoal(deps.client, conversationId, command.maxTokens)
          deps.notify('Objetivo retomado.')
        }
      } catch (error) {
        deps.notify(goalErrorText(error))
      }
    },
    pipeline: (args) => {
      const command = parsePipelineArgs(args)
      if (command.kind === 'error') { deps.notify(command.message); return }
      deps.navigate(pipelineFormPath(conversationId || null, command.pipelineId, command.inputs))
    },
    pipelines: () => {
      deps.navigate(conversationId ? `/pipelines?conversation=${encodeURIComponent(conversationId)}` : '/pipelines')
    },
  }
}
