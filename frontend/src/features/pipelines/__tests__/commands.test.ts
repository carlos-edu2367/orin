import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { BUILTIN_COMMANDS, findBuiltin, parseSlash } from '../../commands/builtins'
import { moreCommandHandlers } from '../../commands/moreCommands'
import { chatCommandHandlers } from '../../commands/useChatCommands'
import { parseGoalArgs, parsePipelineArgs, pipelineFormPath } from '../pipelineCommands'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setup(fetchImpl: ReturnType<typeof vi.fn>, conversationId: string | null = 'conv-1') {
  const notify = vi.fn()
  const openInfo = vi.fn()
  const navigate = vi.fn()
  const send = vi.fn().mockResolvedValue(undefined)
  const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchImpl as unknown as typeof fetch })
  const handlers = moreCommandHandlers({ client, conversationId: conversationId ?? undefined, navigate, send, notify, openInfo })
  return { handlers, notify, openInfo, navigate, send }
}

describe('built-in registration', () => {
  it('lists /goal, /pipeline and /pipelines with a handler, so the picker offers them', () => {
    const { handlers } = setup(vi.fn())
    for (const name of ['goal', 'pipeline', 'pipelines']) {
      expect(BUILTIN_COMMANDS.some((command) => command.name === name)).toBe(true)
      expect(findBuiltin(name)).not.toBeNull()
      expect(typeof handlers[name]).toBe('function')
    }
    const chat = chatCommandHandlers({
      client: new ApiClient({ baseUrl: 'http://localhost', fetchImpl: vi.fn() }), conversationId: 'c', messages: [], title: '', modelId: '',
      provider: '', permissionMode: '', mode: 'chat', workspacePath: null, navigate: vi.fn(), send: vi.fn(), reload: vi.fn(), notify: vi.fn(),
      openInfo: vi.fn(), openPreferences: vi.fn(), openModelPicker: vi.fn(), setPermissionMode: vi.fn(),
    })
    expect(typeof chat.goal).toBe('function')
    expect(typeof chat.pipeline).toBe('function')
    expect(typeof chat.pipelines).toBe('function')
  })

  it('parses the slash text the composer sends', () => {
    expect(parseSlash('/goal escrever os testes --max-tokens 50000')).toEqual({ name: 'goal', args: 'escrever os testes --max-tokens 50000' })
  })
})

describe('/goal parsing', () => {
  it('reads the objective and --max-tokens, wherever the flag is', () => {
    expect(parseGoalArgs('corrigir os testes que falham --max-tokens 50000')).toEqual({ kind: 'start', objective: 'corrigir os testes que falham', maxTokens: 50000 })
    expect(parseGoalArgs('--max-tokens=2.000.000 migrar tudo')).toEqual({ kind: 'start', objective: 'migrar tudo', maxTokens: 2_000_000 })
    expect(parseGoalArgs('migrar tudo')).toEqual({ kind: 'start', objective: 'migrar tudo', maxTokens: null })
  })

  it('reads the sub-commands', () => {
    expect(parseGoalArgs('status')).toEqual({ kind: 'status' })
    expect(parseGoalArgs('clear')).toEqual({ kind: 'clear' })
    expect(parseGoalArgs('resume')).toEqual({ kind: 'resume', maxTokens: null })
    expect(parseGoalArgs('resume --max-tokens 400000')).toEqual({ kind: 'resume', maxTokens: 400000 })
    // a longer sentence that begins with a word is an objective, not a sub-command
    expect(parseGoalArgs('status page nova')).toEqual({ kind: 'start', objective: 'status page nova', maxTokens: null })
  })

  it('refuses a missing objective and a bad cap with the usage text', () => {
    expect(parseGoalArgs('').kind).toBe('error')
    expect(parseGoalArgs('--max-tokens 5000').kind).toBe('error')
    const low = parseGoalArgs('fazer algo --max-tokens 10')
    expect(low).toMatchObject({ kind: 'error' })
    expect(parseGoalArgs('fazer algo --max-tokens 9000000').kind).toBe('error')
    expect(parseGoalArgs('fazer algo --max-tokens abc').kind).toBe('error')
    expect(parseGoalArgs('fazer algo --max-tokens').kind).toBe('error')
  })
})

describe('/pipeline parsing', () => {
  it('reads the id and key=value pairs, with quoted values', () => {
    expect(parsePipelineArgs('implementar-verificar objetivo="criar a tela de login" testes=pytest')).toEqual({
      kind: 'open', pipelineId: 'implementar-verificar', inputs: { objetivo: 'criar a tela de login', testes: 'pytest' },
    })
    expect(parsePipelineArgs('pesquisa')).toEqual({ kind: 'open', pipelineId: 'pesquisa', inputs: {} })
  })

  it('refuses a missing id, a bad id and a pair without "="', () => {
    expect(parsePipelineArgs('').kind).toBe('error')
    expect(parsePipelineArgs('Pesquisa Maiúscula').kind).toBe('error')
    expect(parsePipelineArgs('pesquisa solto').kind).toBe('error')
    expect(parsePipelineArgs('pesquisa =x').kind).toBe('error')
  })

  it('builds the address that opens the form filled in', () => {
    const path = pipelineFormPath('conv-1', 'pesquisa', { tema: 'cota e custo' })
    const query = new URLSearchParams(path.split('?')[1])
    expect(path.startsWith('/pipelines?')).toBe(true)
    expect(query.get('conversation')).toBe('conv-1')
    expect(query.get('pipeline')).toBe('pesquisa')
    expect(query.get('run')).toBe('1')
    expect(query.get('in.tema')).toBe('cota e custo')
  })
})

describe('handlers', () => {
  it('shows the usage text and calls nothing when the argument is missing', async () => {
    const fetchImpl = vi.fn()
    const { handlers, notify, send, navigate } = setup(fetchImpl)
    await handlers.goal('')
    await handlers.pipeline('')
    expect(notify).toHaveBeenCalledTimes(2)
    expect(String(notify.mock.calls[0][0])).toMatch(/Uso: \/goal/)
    expect(String(notify.mock.calls[1][0])).toMatch(/Uso: \/pipeline/)
    expect(fetchImpl).not.toHaveBeenCalled()
    expect(send).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('/goal posts the objective and the cap, and never sends text to the model', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(202, { run_id: 'r1', turn_id: 't1', max_tokens: 50000 }))
    const { handlers, notify, send } = setup(fetchImpl)
    await handlers.goal('ajustar o layout --max-tokens 50000')
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('http://localhost/v1/conversations/conv-1/goal')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ objective: 'ajustar o layout', max_tokens: 50000 })
    expect(String(notify.mock.calls[0][0])).toMatch(/Objetivo iniciado/)
    expect(send).not.toHaveBeenCalled()
  })

  it('/goal explains a refusal in pt-BR', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(409, { error: { code: 'GOAL_ACTIVE', category: 'CONFLICT', message: 'x', retryable: false } }))
    const { handlers, notify } = setup(fetchImpl)
    await handlers.goal('mais um objetivo')
    expect(String(notify.mock.calls[0][0])).toMatch(/Já existe um objetivo ativo/)
  })

  it('/goal status opens the summary, and /goal clear and /goal resume call their routes', async () => {
    const goal = {
      run_id: 'r1', state: 'running', stop_reason: null, objective: 'migrar', max_tokens: 300000, used_tokens: 1000, window_used_tokens: 1000,
      remaining_tokens: 299000, turns: 2, window_turns: 2, max_turns: 50,
      last_evaluation: { turn_index: 1, done: false, reason: 'faltam os testes', model: 'claude-haiku-5-5', fallback: false },
      evaluator_model: 'claude-haiku-5-5', resumable: false, started_at: null, finished_at: null,
    }
    const fetchImpl = vi.fn().mockImplementation(async () => jsonResponse(200, { goal }))
    const { handlers, openInfo, notify } = setup(fetchImpl)
    await handlers.goal('status')
    expect(openInfo).toHaveBeenCalledTimes(1)
    expect(openInfo.mock.calls[0][0].lines.join(' ')).toMatch(/faltam os testes/)
    await handlers.goal('clear')
    await handlers.goal('resume --max-tokens 100000')
    const urls = fetchImpl.mock.calls.map((call) => `${call[1].method ?? 'GET'} ${String(call[0])}`)
    expect(urls).toEqual([
      'GET http://localhost/v1/conversations/conv-1/goal',
      'POST http://localhost/v1/conversations/conv-1/goal/clear',
      'POST http://localhost/v1/conversations/conv-1/goal/resume',
    ])
    expect(JSON.parse(fetchImpl.mock.calls[2][1].body)).toEqual({ max_tokens: 100000 })
    expect(notify).toHaveBeenCalledTimes(2)
  })

  it('/pipeline and /pipelines navigate instead of calling the API', async () => {
    const fetchImpl = vi.fn()
    const { handlers, navigate } = setup(fetchImpl)
    await handlers.pipeline('pesquisa tema="cota"')
    await handlers.pipelines('')
    expect(String(navigate.mock.calls[0][0])).toContain('pipeline=pesquisa')
    expect(String(navigate.mock.calls[0][0])).toContain('in.tema=cota')
    expect(navigate.mock.calls[1][0]).toBe('/pipelines?conversation=conv-1')
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('/goal without a conversation says so', async () => {
    const fetchImpl = vi.fn()
    const { handlers, notify } = setup(fetchImpl, null)
    await handlers.goal('algo')
    expect(String(notify.mock.calls[0][0])).toMatch(/Abra uma conversa/)
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})
