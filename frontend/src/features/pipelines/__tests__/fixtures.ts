import { vi } from 'vitest'
import { ApiClient } from '../../../api/client'

type Reply = unknown | { __status: number; body: unknown }
export type Handler = (request: { url: URL; body: unknown; method: string }) => Reply | Promise<Reply>

export function reply(status: number, body: unknown): { __status: number; body: unknown } {
  return { __status: status, body }
}

export function apiError(status: number, code: string, message: string, details?: Record<string, unknown>) {
  return reply(status, { error: { code, category: status === 409 ? 'CONFLICT' : 'VALIDATION', message_key: code, message, retryable: false, ...(details ? { details } : {}) } })
}

/** A fetch double that answers by `"METHOD /path"` and records every call. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Array<{ key: string; body: unknown; query: URLSearchParams }> = []
  const fetchImpl = vi.fn(async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
    const url = new URL(String(input), 'http://localhost')
    const method = init?.method ?? 'GET'
    const key = `${method} ${url.pathname}`
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ key, body, query: url.searchParams })
    const handler = routes[key]
    if (!handler) return new Response(JSON.stringify({ error: { code: 'NOT_MOCKED', category: 'VALIDATION', message: key } }), { status: 404, headers: { 'Content-Type': 'application/json' } })
    const result = (await handler({ url, body, method })) as { __status?: number; body?: unknown }
    const status = typeof result?.__status === 'number' ? result.__status : 200
    const payload = typeof result?.__status === 'number' ? result.body : result
    return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
  })
  const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchImpl as unknown as typeof fetch, maxAttempts: 1 })
  return { client, calls, fetchImpl, count: (key: string) => calls.filter((call) => call.key === key).length }
}

export function rawPipeline(over: Record<string, unknown> = {}) {
  return {
    id: 'implementar-verificar',
    source: 'builtin',
    definition_hash: 'a'.repeat(64),
    valid: true,
    errors: [],
    warnings: [],
    name: 'Implementar e verificar',
    description: 'Planeja, implementa em paralelo, roda os testes e corrige até passar.',
    inputs: [
      { name: 'objetivo', type: 'string', required: true, default: null, max_length: 8000, agent_settable: true, whole_command: false },
      { name: 'testes', type: 'string', required: false, default: 'pytest -q', max_length: null, agent_settable: false, whole_command: true },
    ],
    steps: [
      { id: 'plano', name: 'Plano', kind: 'agent' },
      { id: 'implementar', name: 'Implementar', kind: 'agent' },
      { id: 'verificar', name: 'Verificar', kind: 'gate' },
      { id: 'corrigir', name: 'Corrigir', kind: 'agent' },
    ],
    approval_required: false,
    approved: true,
    agent_approved: true,
    gate_commands: 1,
    ...over,
  }
}

export function rawDetail(over: Record<string, unknown> = {}) {
  return {
    ...rawPipeline(),
    text: 'version: 1\nid: implementar-verificar\nname: Implementar e verificar\nsteps:\n  - id: plano\n    agent: plan\n    prompt: x\n  - id: verificar\n    gate: {command: pytest}\n    on_fail:\n      goto: corrigir\n      max_loops: 2\n',
    review: {
      pipeline_id: 'implementar-verificar',
      definition_hash: 'a'.repeat(64),
      notice: 'Estes comandos rodam na sua máquina, com as suas permissões.',
      commands: [{ step_id: 'verificar', command: 'pytest -q', expect: 'zero', timeout_seconds: 600, inputs: ['testes'], whole_value: true }],
    },
    context: { errors: [], warnings: [] },
    ...over,
  }
}

export function rawList(over: Record<string, unknown> = {}) {
  return {
    enabled: true,
    pipelines: [rawPipeline()],
    shadowed: [],
    project_skipped: 0,
    errors: [],
    ...over,
  }
}

export function rawEstimate(over: Record<string, unknown> = {}) {
  return {
    pipeline_id: 'implementar-verificar',
    definition_hash: 'a'.repeat(64),
    source: 'builtin',
    steps: [
      { id: 'plano', name: 'Plano', kind: 'agent', agent: 'plan', provider: 'claude', model: 'claude-sonnet-5-5', effort: 'high', isolation: null, map: false, max_parallel: 1 },
      { id: 'implementar', name: 'Implementar', kind: 'agent', agent: 'general', provider: 'claude', model: 'claude-sonnet-5-5', effort: 'medium', isolation: null, map: true, max_parallel: 3 },
      { id: 'verificar', name: 'Verificar', kind: 'gate', agent: null, provider: null, model: null, effort: null, isolation: null, map: false, max_parallel: 1 },
    ],
    subscriptions: [{ provider: 'claude', model: 'claude-sonnet-5-5', steps: ['plano', 'implementar'] }],
    max_parallel: 3,
    budget: { max_tokens: 2000000, max_cost_usd: 15, max_minutes: 120, is_default: false, lowered_by_user_ceiling: false },
    quota: { available: true, warn: [], blocked: [] },
    inputs: { objetivo: 'x', testes: 'pytest -q' },
    degraded: [],
    permission_mode: 'accept_edits',
    notices: ['Isto lista os modelos e as assinaturas envolvidos; não é uma estimativa de tokens.'],
    ...over,
  }
}

export function rawStep(over: Record<string, unknown> = {}) {
  return {
    step_id: 'plano', item_key: '', attempt: 1, state: 'succeeded', agent_id: 'agent-1', loop_count: 0, input_tokens: 1000, output_tokens: 200,
    cost_usd: 0.5, error: null, has_output: true, output_ref: null, started_at: '2026-11-23T10:00:00Z', finished_at: '2026-11-23T10:01:00Z', ...over,
  }
}

export function usageRow(key: string, over: Record<string, unknown> = {}) {
  return { key, attempts: 1, input_tokens: 1000, output_tokens: 200, total_tokens: 1200, cost_usd: 0.5, cost_known: true, latest_states: {}, ...over }
}

export function rawRun(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run-1',
    pipeline_id: 'implementar-verificar',
    source: 'builtin',
    state: 'running',
    stop_reason: null,
    conversation_id: 'conv-1',
    turn_id: 'turn-1',
    definition_hash: 'a'.repeat(64),
    inputs: { objetivo: 'x' },
    used_tokens: 5000,
    used_cost_usd: 0.9,
    used_seconds: 95,
    started_at: '2026-11-23T10:00:00Z',
    finished_at: null,
    resumable: false,
    needs_budget_override: false,
    live_here: true,
    steps: [
      rawStep(),
      rawStep({ step_id: 'implementar', item_key: '000-aa', agent_id: 'agent-2', input_tokens: 2000, output_tokens: 300, cost_usd: null }),
      rawStep({ step_id: 'implementar', item_key: '001-bb', agent_id: 'agent-3', state: 'running', input_tokens: 100, output_tokens: 10, cost_usd: 0.01, has_output: false }),
    ],
    summary: {
      run_id: 'run-1',
      pipeline_id: 'implementar-verificar',
      state: 'running',
      stop_reason: null,
      total: usageRow('total', { attempts: 3, input_tokens: 3100, output_tokens: 510, total_tokens: 3610, cost_usd: 0.51, cost_known: false }),
      by_step: [usageRow('plano'), usageRow('implementar', { attempts: 2, cost_usd: 0.01, cost_known: false })],
      by_agent: [usageRow('agent-1'), usageRow('agent-2', { cost_usd: null, cost_known: false })],
      by_provider: [usageRow('claude', { attempts: 3, total_tokens: 3610, cost_known: false })],
      run_used: { tokens: 5000, cost_usd: 0.9, seconds: 95 },
      consistent: true,
    },
    ...over,
  }
}

export function rawGoal(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run-goal', state: 'running', stop_reason: null, objective: 'Migrar o módulo', max_tokens: 300000, used_tokens: 1000,
    window_used_tokens: 1000, remaining_tokens: 299000, turns: 1, window_turns: 1, max_turns: 50, last_evaluation: null, evaluator_model: null,
    resumable: false, started_at: null, finished_at: null, ...over,
  }
}
