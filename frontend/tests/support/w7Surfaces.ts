/** Fixtures for the W7 Batch D2 surfaces (browser tests): /context, /tasks, the transcript and the status line. */
import type { ExtraRoute } from './w7Chat'

export const CONTEXT_REPORT = {
  conversation_id: 'conv-w7', available: true, limit_tokens: 200_000, used_tokens: 61_400, percentage: 30.7, compaction_count: 1,
  measured_at: '2026-10-20T12:00:09.000Z',
  categories: [
    { key: 'system_prompt', tokens: 9_800 }, { key: 'memory', tokens: 2_100 }, { key: 'tools', tokens: 14_500 },
    { key: 'mcp_tools', tokens: 6_000 }, { key: 'skills', tokens: 3_000 }, { key: 'messages', tokens: 26_000 }, { key: 'free', tokens: 138_600 },
  ],
}

const usage = { input_tokens: 0, output_tokens: 0, total_tokens: 0, usage_reported: true }
export const TASKS = [
  { task_id: 'shell:4242', kind: 'shell', pid: 4242, command: 'npm run dev', description: 'Servidor de desenvolvimento', started_at: '2026-10-20T12:00:05.000Z', url: 'http://localhost:5173', state: 'running', stoppable: true, log_tail: 'VITE v8 ready in 312 ms\n➜ Local: http://localhost:5173/' },
  { task_id: 'shell:4100', kind: 'shell', pid: 4100, command: 'pytest -q tests/billing', description: '', started_at: '2026-10-20T11:58:00.000Z', url: null, state: 'finished', stoppable: false, log_tail: '42 passed in 3.1s' },
]
export const OVERVIEW = {
  conversation_id: 'conv-w7', title: 'Refatorar o módulo de pagamentos', state: 'completed', provider: 'openrouter', model_id: 'm',
  token_usage: usage, tools: [], messages: [], errors: [], turns: [], activity_count: 0, duration_seconds: null,
  agents: [
    { agent_id: 'agent:main', name: 'Main', role: 'main', parent_agent_id: null, provider: 'p', model_id: 'm', state: 'completed', token_usage: usage },
    { agent_id: 'agent:rev', name: 'Revisor', role: 'reviewer', parent_agent_id: 'agent:main', provider: 'p', model_id: 'm', state: 'working', token_usage: usage },
  ],
}

/** The server's activity rows for the existing turn t1: reasoning, a command, a hook that blocked. */
export const TRANSCRIPT_ACTIVITIES = [
  { event_id: 'a1', cursor: '1', turn_id: 't1', agent_id: 'agent:main', event_type: 'agent.thought_delta', summary: 'Pensando', occurred_at: '2026-10-20T12:00:02.000Z', payload: { delta: 'Preciso ler billing/charge.py antes de explicar o fluxo.' } },
  { event_id: 'a2', cursor: '2', turn_id: 't1', agent_id: 'agent:main', event_type: 'tool.finished', summary: 'Leu billing/charge.py', occurred_at: '2026-10-20T12:00:04.000Z', payload: { tool_name: 'read_file', path: 'billing/charge.py', invocation_id: 'i1', status: 'succeeded' } },
  { event_id: 'a3', cursor: '3', turn_id: 't1', agent_id: 'agent:main', event_type: 'plugin.hook_executed', summary: 'Hook PostToolUse (project): blocked', occurred_at: '2026-10-20T12:00:05.000Z', payload: { hook_event: 'PostToolUse', source: 'project', type: 'command', status: 'blocked', exit_code: 2, blocked: true, reason: 'O lint encontrou 2 erros', duration_ms: 812 } },
]

export const STATUS_LINE = { enabled: true, lines: ['\u001b[1;35mOrin\u001b[0m · \u001b[36mpagamentos\u001b[0m · \u001b[32mmain\u001b[0m · contexto 31%'], error: null }

/** Answers the D2 routes (`extra` for `mockChat`); `stopped` records a stop. */
export function surfacesRoutes(options: { stopped?: string[]; contextAvailable?: boolean; statusLine?: unknown } = {}): ExtraRoute {
  let tasks = TASKS
  return ({ method, path }) => {
    if (method === 'GET' && path.endsWith('/context')) return { body: options.contextAvailable === false ? { available: false, categories: [] } : CONTEXT_REPORT }
    if (method === 'GET' && path.endsWith('/tasks')) return { body: { tasks } }
    if (method === 'POST' && path.includes('/tasks/') && path.endsWith('/stop')) {
      const id = decodeURIComponent(path.split('/tasks/')[1].replace(/\/stop$/, ''))
      options.stopped?.push(id)
      tasks = tasks.map((task) => (task.task_id === id ? { ...task, state: 'finished', stoppable: false } : task))
      return { body: { task_id: id, stopped: true, tasks } }
    }
    if (method === 'GET' && path.endsWith('/overview')) return { body: OVERVIEW }
    if (method === 'POST' && path.endsWith('/statusline')) return { body: options.statusLine ?? STATUS_LINE }
    return undefined
  }
}
