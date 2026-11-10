import { afterEach, describe, expect, it, vi } from 'vitest'
import { noticeForEvent } from '../agentNotices'
import { playNotifySound, resetNotifySoundForTests } from '../notifySound'
import { notifySystem, windowIsAway } from '../systemNotify'

const NOW = Date.parse('2026-11-10T15:00:00Z')
const FRESH_AT = '2026-11-10T14:59:45Z'
const STALE_AT = '2026-11-10T14:50:00Z'

describe('noticeForEvent', () => {
  it('notifica pedido de permissão do agente principal e de subagente com nome e ferramenta', () => {
    const mainPerm = noticeForEvent(
      {
        eventId: 'perm-main',
        type: 'tool.permission_required',
        agentId: 'agent:main',
        toolName: 'Bash',
        occurredAt: FRESH_AT,
      },
      { now: NOW },
    )
    expect(mainPerm).toEqual({
      kind: 'attention',
      title: 'Orin precisa da sua permissão',
      body: 'Autorize o uso da ferramenta Bash para continuar.',
    })

    const subPerm = noticeForEvent(
      {
        eventId: 'perm-sub',
        type: 'tool.permission_required',
        agentId: 'agent:sub-1',
        agentName: 'claude-2',
        parentAgentId: 'agent:main',
        toolPermission: {
          requestId: 'req-1',
          toolName: 'Edit',
        },
        occurredAt: FRESH_AT,
      },
      { now: NOW },
    )
    expect(subPerm).toEqual({
      kind: 'attention',
      title: 'claude-2 precisa da sua permissão',
      body: 'Autorize o uso da ferramenta Edit para continuar.',
    })
  })

  it('notifica perguntas, confiança de pasta e decisões do Modo Code, ignorando host.request interno do SDK', () => {
    expect(
      noticeForEvent(
        {
          eventId: 'wait-1',
          type: 'turn.waiting_user',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toEqual({
      kind: 'attention',
      title: 'O Orin está esperando você',
      body: 'Há uma pergunta para você responder na conversa.',
    })

    expect(
      noticeForEvent(
        {
          eventId: 'trust-1',
          type: 'workspace.trust_required',
          workspaceTrust: { path: '/repo/app', items: ['hooks'] },
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toEqual({
      kind: 'attention',
      title: 'Confiança na pasta necessária',
      body: 'Confirme se confia na pasta /repo/app para continuar.',
    })

    expect(
      noticeForEvent(
        {
          eventId: 'dec-1',
          type: 'code_mode.decision_required',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toEqual({
      kind: 'attention',
      title: 'Decisão necessária no Modo Code',
      body: 'O Orin precisa da sua confirmação para continuar.',
    })

    // Internal W6 SDK host callback request should not notify the human user
    expect(
      noticeForEvent(
        {
          eventId: 'host-sdk',
          type: 'host.request',
          payload: { kind: 'hook_callback', request_id: 'host_123' },
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toBeNull()

    // User-facing host.request should notify
    expect(
      noticeForEvent(
        {
          eventId: 'host-user',
          type: 'host.request',
          payload: { kind: 'user_question', user_facing: true },
          summary: 'Escolha o ambiente de destino.',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toEqual({
      kind: 'attention',
      title: 'O Orin precisa da sua confirmação',
      body: 'Escolha o ambiente de destino.',
    })
  })

  it('notifica conclusão no chat e no modo code sem duplicar turn.completed do mesmo turno', () => {
    const shownIds = new Set<string>()

    // Chat turn completion
    const chatDone = noticeForEvent(
      {
        eventId: 'chat-done-1',
        type: 'turn.completed',
        turnId: 'turn-chat-1',
        occurredAt: FRESH_AT,
      },
      { mode: 'chat', shownIds, now: NOW },
    )
    expect(chatDone).toEqual({
      kind: 'done',
      title: 'Orin respondeu',
      body: 'A resposta está pronta.',
    })

    // Code mode completion followed by turn.completed for the same turn
    const codeDone = noticeForEvent(
      {
        eventId: 'code-done-1',
        type: 'code_mode.completed',
        turnId: 'turn-code-1',
        occurredAt: FRESH_AT,
      },
      { mode: 'code', shownIds, now: NOW },
    )
    expect(codeDone).toEqual({
      kind: 'done',
      title: 'Modo Code concluído',
      body: 'A entrega está pronta para sua revisão.',
    })

    const duplicateTurnDone = noticeForEvent(
      {
        eventId: 'code-turn-done-1',
        type: 'turn.completed',
        turnId: 'turn-code-1',
        occurredAt: FRESH_AT,
      },
      { mode: 'code', shownIds, now: NOW },
    )
    expect(duplicateTurnDone).toBeNull()

    // Code mode with caveats on another turn
    const caveatsDone = noticeForEvent(
      {
        eventId: 'code-caveats-2',
        type: 'code_mode.completed_with_caveats',
        turnId: 'turn-code-2',
        occurredAt: FRESH_AT,
      },
      { mode: 'code', shownIds, now: NOW },
    )
    expect(caveatsDone).toEqual({
      kind: 'done',
      title: 'Modo Code concluído com ressalvas',
      body: 'A entrega está pronta, mas há uma limitação documentada.',
    })
  })

  it('notifica falha e bloqueio do agente principal, mas ignora cancelamento', () => {
    expect(
      noticeForEvent(
        {
          eventId: 'fail-1',
          type: 'turn.failed',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toEqual({
      kind: 'failed',
      title: 'O turno falhou',
      body: 'O Orin encontrou um erro ao concluir a resposta.',
    })

    expect(
      noticeForEvent(
        {
          eventId: 'block-1',
          type: 'code_mode.blocked',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toEqual({
      kind: 'failed',
      title: 'Modo Code bloqueado',
      body: 'O Orin encontrou um bloqueio que precisa da sua decisão.',
    })

    expect(
      noticeForEvent(
        {
          eventId: 'cancel-1',
          type: 'turn.failed',
          errorCode: 'TURN_CANCELLED',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toBeNull()
  })

  it('não notifica conclusão ou falha de subagente', () => {
    expect(
      noticeForEvent(
        {
          eventId: 'sub-done',
          type: 'turn.completed',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toBeNull()

    expect(
      noticeForEvent(
        {
          eventId: 'sub-code-done',
          type: 'code_mode.completed',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toBeNull()

    expect(
      noticeForEvent(
        {
          eventId: 'sub-fail',
          type: 'turn.failed',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          occurredAt: FRESH_AT,
        },
        { now: NOW },
      ),
    ).toBeNull()
  })

  it('respeita as opções desligadas, dedup por eventId e ignora eventos velhos', () => {
    const permEvent = {
      eventId: 'perm-opt-1',
      type: 'tool.permission_required',
      toolName: 'Bash',
      occurredAt: FRESH_AT,
    }
    const doneEvent = {
      eventId: 'done-opt-1',
      type: 'turn.completed',
      occurredAt: FRESH_AT,
    }

    // Master switch off
    expect(noticeForEvent(permEvent, { now: NOW, system_notifications: false })).toBeNull()
    expect(noticeForEvent(doneEvent, { now: NOW, system_notifications: false })).toBeNull()

    // Permission sub-option off
    expect(noticeForEvent(permEvent, { now: NOW, notify_permissions: false })).toBeNull()
    expect(noticeForEvent(doneEvent, { now: NOW, notify_permissions: false })).not.toBeNull()

    // Completion sub-option off
    expect(noticeForEvent(doneEvent, { now: NOW, notify_completion: false })).toBeNull()
    expect(
      noticeForEvent({ ...doneEvent, eventId: 'fail-opt-1', type: 'turn.failed' }, { now: NOW, notify_completion: false }),
    ).toBeNull()
    expect(noticeForEvent(permEvent, { now: NOW, notify_completion: false })).not.toBeNull()

    // Dedup by eventId
    const shownIds = new Set<string>()
    expect(noticeForEvent(permEvent, { now: NOW, shownIds })).not.toBeNull()
    expect(noticeForEvent(permEvent, { now: NOW, shownIds })).toBeNull()

    // Stale event ignored
    expect(
      noticeForEvent(
        {
          eventId: 'stale-1',
          type: 'turn.completed',
          occurredAt: STALE_AT,
        },
        { now: NOW },
      ),
    ).toBeNull()
  })
})

describe('windowIsAway', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  it('considera fora quando a aba está oculta ou quando a janela não tem foco', () => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
    const hasFocusSpy = vi.spyOn(document, 'hasFocus')

    hasFocusSpy.mockReturnValue(true)
    expect(windowIsAway()).toBe(false)

    hasFocusSpy.mockReturnValue(false)
    expect(windowIsAway()).toBe(true)

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    })
    hasFocusSpy.mockReturnValue(true)
    expect(windowIsAway()).toBe(true)
  })
})

describe('notifySound & notifySystem sound integration', () => {
  afterEach(() => {
    resetNotifySoundForTests()
    vi.unstubAllGlobals()
    delete (window as { orinDesktop?: unknown }).orinDesktop
  })

  it('não quebra sem AudioContext e respeita a opção desligada', () => {
    delete (window as unknown as { AudioContext?: unknown }).AudioContext
    expect(() => playNotifySound('attention')).not.toThrow()
    expect(playNotifySound('attention')).toBe(false)
    expect(playNotifySound('done', false)).toBe(false)
  })

  it('sintetiza sons distintos via AudioContext quando habilitado e silencia a notificação nativa no desktop', () => {
    const oscillators: Array<{ type: string; freqs: number[] }> = []
    class FakeAudioContext {
      state = 'running'
      currentTime = 0
      destination = {}
      resume = vi.fn(async () => undefined)
      createOscillator() {
        const record = { type: 'sine', freqs: [] as number[] }
        oscillators.push(record)
        return {
          set type(val: string) {
            record.type = val
          },
          frequency: {
            setValueAtTime: (freq: number) => record.freqs.push(freq),
            exponentialRampToValueAtTime: (freq: number) => record.freqs.push(freq),
          },
          connect: vi.fn(),
          start: vi.fn(),
          stop: vi.fn(),
        }
      }
      createGain() {
        return {
          gain: {
            setValueAtTime: vi.fn(),
            exponentialRampToValueAtTime: vi.fn(),
          },
          connect: vi.fn(),
        }
      }
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)

    expect(playNotifySound('attention', false)).toBe(false)
    expect(oscillators).toHaveLength(0)

    expect(playNotifySound('attention', true)).toBe(true)
    expect(oscillators).toHaveLength(2)
    expect(oscillators[1].freqs[0]).toBeGreaterThan(oscillators[0].freqs[0])

    expect(playNotifySound('done', true)).toBe(true)
    expect(oscillators).toHaveLength(5) // +3 chord notes

    expect(playNotifySound('failed', true)).toBe(true)
    expect(oscillators).toHaveLength(6) // +1 low descending note

    const notifyCodeMode = vi.fn(async () => true)
    ;(window as { orinDesktop?: unknown }).orinDesktop = { notifyCodeMode }

    expect(
      notifySystem({
        title: 'Orin respondeu',
        body: 'A resposta está pronta.',
        kind: 'done',
        sound: true,
      }),
    ).toBe(true)
    expect(notifyCodeMode).toHaveBeenCalledWith({
      title: 'Orin respondeu',
      body: 'A resposta está pronta.',
      silent: true,
    })

    expect(
      notifySystem({
        title: 'Orin respondeu',
        body: 'A resposta está pronta.',
        kind: 'done',
        sound: false,
      }),
    ).toBe(true)
    expect(notifyCodeMode).toHaveBeenLastCalledWith({
      title: 'Orin respondeu',
      body: 'A resposta está pronta.',
      silent: false,
    })
  })
})
