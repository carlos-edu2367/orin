import { mkdirSync } from 'node:fs'
import { env } from 'node:process'
import { expect, test, type Page } from '@playwright/test'
import { CONVERSATION_ID, mockChat, setTheme } from '../support/w7Chat'

/**
 * 0.13 WS-C11: the pipeline screens and the /goal banner at 1280x800 behind a mocked API. Nothing scrolls sideways.
 * Set `PIPELINES_SHOTS_DIR` to also save screenshots (used by the manual visual check).
 */

const HASH = 'a'.repeat(64)

const PIPELINE = {
  id: 'implementar-verificar',
  source: 'builtin',
  definition_hash: HASH,
  valid: true,
  errors: [],
  warnings: [],
  name: 'Implementar e verificar',
  description: 'Planeja, implementa em paralelo, roda os testes e corrige até passar.',
  inputs: [
    { name: 'objetivo', type: 'string', required: true, default: null, max_length: 8000, agent_settable: true, whole_command: false },
    { name: 'tentativas', type: 'integer', required: false, default: 2, max_length: null, agent_settable: true, whole_command: false },
    { name: 'revisar', type: 'boolean', required: false, default: true, max_length: null, agent_settable: true, whole_command: false },
    { name: 'modelos', type: 'models', required: false, default: 'auto', max_length: null, agent_settable: true, whole_command: false },
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
}

const LIST = {
  enabled: true,
  pipelines: [
    PIPELINE,
    { ...PIPELINE, id: 'pesquisa', name: 'Pesquisa em paralelo', description: 'Três agentes pesquisam e um consolida.', gate_commands: 0 },
    { ...PIPELINE, id: 'revisao-cruzada', source: 'user', name: 'Revisão cruzada', description: 'Dois modelos revisam o diff.', gate_commands: 0 },
    {
      ...PIPELINE, id: 'quebrado', source: 'user', name: 'quebrado', valid: false, steps: [], inputs: [], description: '',
      errors: [{ code: 'E_UNKNOWN_KEY', message: 'chave desconhecida `agnt` (você quis dizer `agent`?)', line: 7, column: 5, path: 'steps[0].agnt' }],
    },
    { ...PIPELINE, id: 'deploy-homolog', source: 'project', name: 'Deploy em homologação', approval_required: true, approved: false, agent_approved: false, gate_commands: 2 },
  ],
  shadowed: [{ id: 'pesquisa', source: 'builtin', definition_hash: 'b'.repeat(64) }],
  project_skipped: 0,
  errors: [],
}

const DETAIL = {
  ...PIPELINE,
  text: 'version: 1\nid: implementar-verificar\nname: Implementar e verificar\nsteps:\n  - id: plano\n    agent: plan\n  - id: verificar\n    gate: {command: pytest -q}\n    on_fail:\n      goto: corrigir\n      max_loops: 2\n',
  review: {
    pipeline_id: 'implementar-verificar',
    definition_hash: HASH,
    notice: 'Estes comandos rodam na sua máquina, com as suas permissões.',
    commands: [{ step_id: 'verificar', command: 'pytest -q', expect: 'zero', timeout_seconds: 600, inputs: [], whole_value: false }],
  },
  context: { errors: [], warnings: [] },
}

const ESTIMATE = {
  pipeline_id: 'implementar-verificar',
  definition_hash: HASH,
  source: 'builtin',
  steps: [
    { id: 'plano', name: 'Plano', kind: 'agent', agent: 'plan', provider: 'anthropic-cli', model: 'claude-sonnet-5-5', effort: 'high', isolation: null, map: false, max_parallel: 1 },
    { id: 'implementar', name: 'Implementar', kind: 'agent', agent: 'general', provider: 'openai-codex', model: 'gpt-6-luna', effort: 'medium', isolation: 'worktree', map: true, max_parallel: 3 },
    { id: 'verificar', name: 'Verificar', kind: 'gate', agent: null, provider: null, model: null, effort: null, isolation: null, map: false, max_parallel: 1 },
    { id: 'corrigir', name: 'Corrigir', kind: 'agent', agent: 'general', provider: 'anthropic-cli', model: 'claude-sonnet-5-5', effort: 'medium', isolation: null, map: false, max_parallel: 1 },
  ],
  subscriptions: [
    { provider: 'anthropic-cli', model: 'claude-sonnet-5-5', steps: ['plano', 'corrigir'] },
    { provider: 'openai-codex', model: 'gpt-6-luna', steps: ['implementar'] },
  ],
  max_parallel: 3,
  budget: { max_tokens: 2000000, max_cost_usd: 15, max_minutes: 120, is_default: false, lowered_by_user_ceiling: false },
  quota: { available: true, warn: ['openai-codex'], blocked: [] },
  inputs: { objetivo: '', tentativas: 2, revisar: true, modelos: ['claude-sonnet-5-5', 'gpt-6-luna'] },
  degraded: [{ reason: 'isolation_unavailable', detail: 'Sem Git na pasta: worktree indisponível', effect: 'as etapas em paralelo rodam na mesma pasta' }],
  permission_mode: 'ask_permissions',
  notices: ['Isto lista os modelos e as assinaturas envolvidos; não é uma estimativa de tokens.'],
}

function step(over: Record<string, unknown>) {
  return {
    step_id: 'plano', item_key: '', attempt: 1, state: 'succeeded', agent_id: 'agent-1', loop_count: 0, input_tokens: 18000, output_tokens: 2400,
    cost_usd: 0.31, error: null, has_output: true, output_ref: null, started_at: '2026-11-23T10:00:00Z', finished_at: '2026-11-23T10:01:00Z', ...over,
  }
}

function usage(key: string, over: Record<string, unknown> = {}) {
  return { key, attempts: 1, input_tokens: 18000, output_tokens: 2400, total_tokens: 20400, cost_usd: 0.31, cost_known: true, latest_states: {}, ...over }
}

const RUN = {
  run_id: 'run-1',
  pipeline_id: 'implementar-verificar',
  source: 'builtin',
  state: 'running',
  stop_reason: null,
  conversation_id: CONVERSATION_ID,
  turn_id: 't2',
  definition_hash: HASH,
  inputs: { objetivo: 'Migrar o módulo de pagamentos para o novo SDK' },
  used_tokens: 142000,
  used_cost_usd: 2.37,
  used_seconds: 640,
  started_at: '2026-11-23T10:00:00Z',
  finished_at: null,
  resumable: false,
  needs_budget_override: false,
  live_here: true,
  steps: [
    step({}),
    step({ step_id: 'implementar', item_key: '000-api', agent_id: 'agent-2', input_tokens: 34000, output_tokens: 6100, cost_usd: null }),
    step({ step_id: 'implementar', item_key: '001-cli', agent_id: 'agent-3', input_tokens: 29000, output_tokens: 5200, cost_usd: null }),
    step({ step_id: 'verificar', state: 'gate_failed', agent_id: null, input_tokens: 0, output_tokens: 0, cost_usd: null, has_output: true }),
    step({ step_id: 'corrigir', agent_id: 'agent-4', input_tokens: 21000, output_tokens: 3300, cost_usd: 0.42 }),
    step({ step_id: 'verificar', loop_count: 1, state: 'running', agent_id: null, input_tokens: 0, output_tokens: 0, cost_usd: null, has_output: false, finished_at: null }),
  ],
  summary: {
    run_id: 'run-1',
    pipeline_id: 'implementar-verificar',
    state: 'running',
    stop_reason: null,
    total: usage('total', { attempts: 6, input_tokens: 102000, output_tokens: 17000, total_tokens: 119000, cost_usd: 0.73, cost_known: false }),
    by_step: [
      usage('plano'),
      usage('implementar', { attempts: 2, input_tokens: 63000, output_tokens: 11300, total_tokens: 74300, cost_usd: null, cost_known: false }),
      usage('verificar', { attempts: 2, input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_usd: 0, cost_known: true }),
      usage('corrigir', { input_tokens: 21000, output_tokens: 3300, total_tokens: 24300, cost_usd: 0.42 }),
    ],
    by_agent: [
      usage('agent-1'),
      usage('agent-2', { input_tokens: 34000, output_tokens: 6100, total_tokens: 40100, cost_usd: null, cost_known: false }),
      usage('agent-3', { input_tokens: 29000, output_tokens: 5200, total_tokens: 34200, cost_usd: null, cost_known: false }),
      usage('agent-4', { input_tokens: 21000, output_tokens: 3300, total_tokens: 24300, cost_usd: 0.42 }),
    ],
    by_provider: [
      usage('anthropic-cli', { attempts: 2, input_tokens: 39000, output_tokens: 5700, total_tokens: 44700, cost_usd: 0.73 }),
      usage('openai-codex', { attempts: 2, input_tokens: 63000, output_tokens: 11300, total_tokens: 74300, cost_usd: null, cost_known: false }),
    ],
    run_used: { tokens: 142000, cost_usd: 2.37, seconds: 640 },
    consistent: true,
  },
}

const GOAL = {
  run_id: 'run-goal',
  state: 'running',
  stop_reason: null,
  objective: 'Migrar o módulo de pagamentos para o novo SDK e deixar os testes de reembolso passando',
  max_tokens: 300000,
  used_tokens: 112000,
  window_used_tokens: 112000,
  remaining_tokens: 188000,
  turns: 3,
  window_turns: 3,
  max_turns: 50,
  last_evaluation: { turn_index: 2, done: false, reason: 'Os testes de reembolso ainda não passam (3 falhas em test_refund.py)', model: 'claude-sonnet-5-5', fallback: true },
  evaluator_model: 'claude-sonnet-5-5',
  resumable: false,
  started_at: '2026-11-23T10:00:00Z',
  finished_at: null,
}

async function openWithApi(page: Page, url: string, goal: Record<string, unknown> = GOAL) {
  await setTheme(page, 'dark')
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page, ({ method, path }) => {
    if (method === 'GET' && path === '/v1/pipelines') return { body: LIST }
    if (method === 'GET' && path === '/v1/pipelines/implementar-verificar') return { body: DETAIL }
    if (method === 'POST' && path === '/v1/pipelines/implementar-verificar/estimate') return { body: ESTIMATE }
    if (method === 'GET' && path === '/v1/pipeline-runs') return { body: { runs: [] } }
    if (method === 'GET' && path === '/v1/pipeline-runs/run-1') return { body: RUN }
    if (method === 'GET' && path === `/v1/conversations/${CONVERSATION_ID}/goal`) return { body: { goal } }
    return undefined
  })
  // The pipeline screens are documents too: give them the same CSRF bootstrap the chat route gets.
  await page.route('**/pipelines**', async (route) => {
    if (route.request().resourceType() !== 'document') { await route.fallback(); return }
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-w7-e2e">')
    await route.fulfill({ response, body })
  })
  await page.goto(url)
}

async function expectNoSideScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
}

async function shot(page: Page, name: string) {
  const dir = env.PIPELINES_SHOTS_DIR
  if (!dir) return
  mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: `${dir}/${name}.png` })
}

test('pipelines panel: list by origin, errors with line, shadowed, definition with the commands to review', async ({ page }) => {
  await openWithApi(page, `/pipelines?conversation=${CONVERSATION_ID}&pipeline=implementar-verificar`)
  await expect(page.getByTestId('pipeline-row-quebrado')).toContainText('Com erros')
  await expect(page.getByTestId('pipeline-shadowed')).toContainText('pesquisa')
  await page.getByRole('tab', { name: 'Definição' }).click()
  await expect(page.getByText('pytest -q').first()).toBeVisible()
  await expectNoSideScroll(page)
  await shot(page, '01-painel-pipelines')
})

test('run form: inputs by type, subscriptions, budget, quota and degradation before spending', async ({ page }) => {
  await openWithApi(page, `/pipelines?conversation=${CONVERSATION_ID}&pipeline=implementar-verificar`)
  await expect(page.getByText('gpt-6-luna').first()).toBeVisible()
  await expectNoSideScroll(page)
  await shot(page, '02-formulario-execucao')
  await page.mouse.move(900, 500)
  await page.mouse.wheel(0, 650)
  await page.waitForTimeout(200)
  await shot(page, '02b-formulario-execucao-rodape')
})

test('run screen: steps, a loop, tokens and cost per step and per agent', async ({ page }) => {
  await openWithApi(page, '/pipelines/runs/run-1')
  await expect(page.getByTestId('pipeline-run-page')).toContainText('volta 1')
  await expectNoSideScroll(page)
  await shot(page, '03-execucao-com-laco')
  await page.mouse.move(900, 500)
  await page.mouse.wheel(0, 650)
  await page.waitForTimeout(200)
  await shot(page, '03b-execucao-custos')
})

test('/goal banner: evaluator with fallback, last reason and remaining tokens', async ({ page }) => {
  await openWithApi(page, `/chats/${CONVERSATION_ID}`)
  const banner = page.getByTestId('goal-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('restam')
  await expectNoSideScroll(page)
  await shot(page, '04-banner-goal')
})

test('/goal banner: stopped on its ceiling offers "Retomar" with a new cap', async ({ page }) => {
  const stopped = { ...GOAL, state: 'budget_exceeded', stop_reason: 'budget', resumable: true, window_used_tokens: 300000, used_tokens: 300000, remaining_tokens: 0 }
  await openWithApi(page, `/chats/${CONVERSATION_ID}`, stopped)
  const banner = page.getByTestId('goal-banner')
  await expect(banner.getByRole('button', { name: 'Retomar' })).toBeVisible()
  await expectNoSideScroll(page)
  await shot(page, '05-banner-goal-retomar')
})
