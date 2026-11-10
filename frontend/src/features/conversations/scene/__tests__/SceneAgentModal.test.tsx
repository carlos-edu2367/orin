import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ConversationActivityEvent } from '../../activityTypes'
import {
  SceneAgentModal,
  resolveAgentModalIdentity,
} from '../SceneAgentModal'
import { computeBubbleBox } from '../SceneCanvas'
import { SceneView } from '../SceneView'
import { stationSlot } from '../sceneLayout'
import { MAIN_AGENT_ID, type SceneState } from '../sceneTypes'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// Client coordinates corresponding to a logical point (x, y) on the default 2x scaled canvas (640x384).
function logicalToClient(logicalX: number, logicalY: number, scale = 2) {
  return {
    clientX: Math.round(logicalX * scale),
    clientY: Math.round(logicalY * scale),
  }
}

describe('SceneAgentModal & integração com SceneView / SceneCanvas', () => {
  it('(1) clicar num personagem no canvas seleciona no HUD e abre o modal com nome, tag, status, missão, ferramenta, fala longa completa (lastSpeech), pensamento completo e diário com mais recente no topo', () => {
    const longSpeech =
      'Encontrei três pontos críticos na camada de autenticação que precisam de refatoração imediata antes de liberar a versão.'
    const longThought =
      'Analisando o fluxo completo de tokens JWT, expiração de sessão e validação de assinatura nos middlewares da API.'

    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-turn',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        turnId: 'turn-modal-1',
        agentId: MAIN_AGENT_ID,
        summary: 'Coordenando revisão',
      },
      {
        eventId: 'ev-sub-created',
        cursor: '2',
        type: 'agent.created',
        kind: 'agent',
        state: 'working',
        turnId: 'turn-modal-1',
        agentId: 'agent:conv:claude-1',
        agentName: 'Arquiteto',
        parentAgentId: MAIN_AGENT_ID,
        summary: 'Iniciando arquiteto',
        payload: { subagent_type: 'claude' },
      },
      {
        eventId: 'ev-sub-start',
        cursor: '3',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        turnId: 'turn-modal-1',
        agentId: 'agent:conv:claude-1',
        agentName: 'Arquiteto',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'read_file',
        summary: 'Inspecionar módulo de autenticação',
        occurredAt: '2026-11-10T14:10:00.000Z',
      },
      {
        eventId: 'ev-sub-tool-2',
        cursor: '4',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        turnId: 'turn-modal-1',
        agentId: 'agent:conv:claude-1',
        agentName: 'Arquiteto',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'search_code',
        summary: 'Auditar validação de sessão',
        occurredAt: '2026-11-10T14:12:00.000Z',
      },
      {
        eventId: 'ev-sub-thought',
        cursor: '5',
        type: 'agent.thought_delta',
        kind: 'agent',
        state: 'working',
        turnId: 'turn-modal-1',
        agentId: 'agent:conv:claude-1',
        agentName: 'Arquiteto',
        summary: '',
        delta: longThought,
      },
      {
        eventId: 'ev-sub-speech',
        cursor: '6',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        turnId: 'turn-modal-1',
        agentId: 'agent:conv:claude-1',
        agentName: 'Arquiteto',
        summary: '',
        content: longSpeech,
      },
    ]

    render(<SceneView events={events} running={true} />)

    // Antes de clicar no canvas, o modal não está aberto
    expect(screen.queryByRole('dialog')).toBeNull()

    // 'search_code' posiciona o subagente na estação 'files' (slot 0)
    const filesSlot = stationSlot('files', 0)
    const canvas = screen.getByTestId('scene-canvas')
    const clickCoords = logicalToClient(filesSlot.x, filesSlot.y - 10)
    fireEvent.click(canvas, clickCoords)

    // Selecionou no HUD E abriu o modal
    expect(screen.getByTestId('scene-agent-panel')).toBeTruthy()
    const dialog = screen.getByRole('dialog')
    expect(dialog).toBeTruthy()
    expect(dialog.getAttribute('aria-modal')).toBe('true')

    // Nome, tag, status e duração
    expect(within(dialog).getByTestId('scene-agent-modal-name').textContent).toBe(
      'Arquiteto',
    )
    const tagText =
      within(dialog).getByTestId('scene-agent-modal-tag').textContent ?? ''
    expect(tagText).toContain('claude')
    expect(tagText).toContain('claude-1')

    const statusText =
      within(dialog).getByTestId('scene-agent-modal-status').textContent ?? ''
    expect(statusText).toContain('Trabalhando')

    // Missão e ferramenta atual
    expect(
      within(dialog).getByTestId('scene-agent-modal-mission').textContent,
    ).toContain('Auditar validação de sessão')
    expect(
      within(dialog).getByTestId('scene-agent-modal-tool').textContent,
    ).toContain('search_code')

    // Fala longa completa (sem truncar em 26 ou 60 chars) e pensamento completo
    expect(
      within(dialog).getByTestId('scene-agent-modal-speech').textContent,
    ).toBe(longSpeech)
    expect(
      within(dialog).getByTestId('scene-agent-modal-thought').textContent,
    ).toBe(longThought)

    // Diário com mais recente no topo
    const diaryList = within(dialog).getByTestId('scene-agent-modal-diary-list')
    const items = within(diaryList).getAllByRole('listitem')
    expect(items.length).toBeGreaterThanOrEqual(2)
    expect(items[0]?.textContent).toContain('Auditar validação de sessão')
    expect(items[1]?.textContent).toContain('Inspecionar módulo de autenticação')
  })

  it('(2) clicar num botão da lista do HUD apenas seleciona (não abre modal), e Enter/Espaço no canvas focado ou botão "Abrir detalhes" abrem o modal', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:conv:sub-1',
        agentName: 'Implementador',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'edit_file',
        summary: 'Editando componente',
        payload: { subagent_type: 'antigravity' },
      },
    ]

    render(<SceneView events={events} running={true} />)

    // Clicar no nome do agente na lista do HUD só seleciona
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:sub-1'))
    expect(screen.getByTestId('scene-agent-panel')).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()

    // Enter no canvas focado com agente selecionado abre o modal
    const canvas = screen.getByTestId('scene-canvas')
    canvas.focus()
    fireEvent.keyDown(canvas, { key: 'Enter' })
    expect(screen.getByRole('dialog')).toBeTruthy()

    // Fechar pelo botão fechar
    fireEvent.click(screen.getByTestId('scene-agent-modal-close'))
    expect(screen.queryByRole('dialog')).toBeNull()

    // Botão "Abrir detalhes" no painel do HUD também abre o modal
    fireEvent.click(screen.getByTestId('scene-agent-open-modal'))
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('(3) Esc, botão fechar ("×") e clique no fundo fecham o modal e devolvem o foco ao canvas, e Tab fica preso dentro do modal', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        toolName: 'run_command',
        summary: 'Rodando verificação',
      },
    ]

    render(<SceneView events={events} running={true} />)

    const canvas = screen.getByTestId('scene-canvas')
    const terminalSlot = stationSlot('terminal', 0)
    fireEvent.click(canvas, logicalToClient(terminalSlot.x, terminalSlot.y - 10))

    const dialog = screen.getByRole('dialog')
    const closeBtn = within(dialog).getByTestId('scene-agent-modal-close')
    expect(document.activeElement).toBe(closeBtn)

    // Tab preso dentro do modal
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: false })
    expect(dialog.contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
    expect(dialog.contains(document.activeElement)).toBe(true)

    // Esc fecha e devolve o foco ao canvas
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(canvas)

    // Reabrir via Espaço com canvas focado e fechar pelo botão "×"
    fireEvent.keyDown(canvas, { key: ' ' })
    const reopened = screen.getByRole('dialog')
    fireEvent.click(within(reopened).getByTestId('scene-agent-modal-close'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(canvas)

    // Reabrir e fechar clicando no backdrop
    fireEvent.keyDown(canvas, { key: 'Enter' })
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.click(screen.getByTestId('scene-agent-modal-backdrop'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(canvas)
  })

  it('(4) clicar em cima do balão de fala do personagem também abre o modal desse agente', () => {
    const speechText =
      'Esta é uma fala bem comprida que aparece cortada no balão do canvas mas completa no modal ao clicar no balão.'
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-turn',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        turnId: 'turn-b',
        agentId: MAIN_AGENT_ID,
        summary: 'Iniciando',
      },
      {
        eventId: 'ev-speech',
        cursor: '2',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        turnId: 'turn-b',
        agentId: MAIN_AGENT_ID,
        summary: '',
        content: speechText,
      },
    ]

    render(<SceneView events={events} running={true} />)

    // Orin está na estação 'meeting' (slot 0)
    const meetingSlot = stationSlot('meeting', 0)
    const bubbleBox = computeBubbleBox({
      x: meetingSlot.x,
      y: meetingSlot.y,
      bubble: {
        kind: 'speech',
        text: speechText.slice(0, 60),
        fullText: speechText,
        eventId: 'ev-speech',
      },
      bubblePopProgress: 0,
    })
    expect(bubbleBox).not.toBeNull()

    // Clicar no centro do balão (acima da cabeça do personagem, fora da hitbox do corpo)
    const bubbleCenterLogicalX = bubbleBox!.boxX + bubbleBox!.boxW / 2
    const bubbleCenterLogicalY = bubbleBox!.boxY + bubbleBox!.boxH / 2
    const canvas = screen.getByTestId('scene-canvas')
    fireEvent.click(
      canvas,
      logicalToClient(bubbleCenterLogicalX, bubbleCenterLogicalY),
    )

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByTestId('scene-agent-modal-name').textContent).toBe(
      'Orin',
    )
    expect(
      within(dialog).getByTestId('scene-agent-modal-speech').textContent,
    ).toBe(speechText)
  })

  it('(5) agente com pendência mostra a pendência dentro do modal e aciona os handlers', async () => {
    const permEvent: ConversationActivityEvent = {
      eventId: 'ev-perm-modal',
      cursor: '1',
      type: 'tool.permission_required',
      kind: 'tool',
      state: 'waiting_user',
      turnId: 'turn-p',
      agentId: 'sub-exec',
      agentName: 'Executor',
      parentAgentId: MAIN_AGENT_ID,
      summary: 'Rodar migração do banco',
      toolPermission: {
        requestId: 'req-modal-1',
        turnId: 'turn-p',
        toolName: 'run_command',
        command: 'npm run db:migrate',
        state: 'pending',
      },
    }

    const onToolPermission = vi.fn().mockResolvedValue(undefined)

    render(
      <SceneView
        events={[permEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-p'])}
        actions={{ onToolPermission }}
      />,
    )

    // Seleciona no HUD e clica em "Abrir detalhes" (ou clica no personagem no canvas)
    fireEvent.click(screen.getByTestId('scene-agent-button-sub-exec'))
    fireEvent.click(screen.getByTestId('scene-agent-open-modal'))

    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByTestId('scene-agent-modal-status').textContent,
    ).toContain('Esperando você')

    const pendingContainer = within(dialog).getByTestId(
      'scene-agent-modal-pending',
    )
    const permCard = within(pendingContainer).getByTestId('tool-permission-card')
    const allowBtn = within(permCard).getByRole('button', { name: 'Permitir' })
    fireEvent.click(allowBtn)

    await waitFor(() => {
      expect(onToolPermission).toHaveBeenCalledWith(permEvent, 'allow_once')
    })
  })

  it('(6) ações Parar (com confirmação) e Enviar mensagem no modal chamam os handlers', async () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-start',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        summary: 'Início',
      },
      {
        eventId: 'ev-sub',
        cursor: '2',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:conv:worker-1',
        agentName: 'Analista',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'read_file',
        summary: 'Lendo contratos',
        payload: { subagent_type: 'Explore' },
      },
    ]

    const onStopAgent = vi.fn().mockResolvedValue({ ok: true })
    const onMessageAgent = vi.fn().mockResolvedValue({ ok: true })

    render(
      <SceneView
        events={events}
        running={true}
        onStopAgent={onStopAgent}
        onMessageAgent={onMessageAgent}
      />,
    )

    // Clicar no personagem Analista na estação 'files'
    const filesSlot = stationSlot('files', 0)
    fireEvent.click(
      screen.getByTestId('scene-canvas'),
      logicalToClient(filesSlot.x, filesSlot.y - 10),
    )

    const dialog = screen.getByRole('dialog')

    // Enviar mensagem pelo modal
    fireEvent.click(within(dialog).getByTestId('scene-agent-message-button'))
    const msgInput = within(dialog).getByTestId('scene-agent-message-input')
    fireEvent.change(msgInput, {
      target: { value: 'Priorize os arquivos de domínio' },
    })
    fireEvent.click(within(dialog).getByTestId('scene-agent-message-submit'))

    await waitFor(() => {
      expect(onMessageAgent).toHaveBeenCalledWith(
        'agent:conv:worker-1',
        'Priorize os arquivos de domínio',
      )
    })
    expect(
      within(dialog).getByTestId('scene-agent-message-success').textContent,
    ).toContain('Mensagem entregue a Analista')

    // Parar com confirmação pelo modal
    fireEvent.click(within(dialog).getByTestId('scene-agent-stop-button'))
    expect(onStopAgent).not.toHaveBeenCalled()
    expect(within(dialog).getByTestId('scene-agent-stop-confirm')).toBeTruthy()

    fireEvent.click(
      within(dialog).getByTestId('scene-agent-stop-confirm-button'),
    )
    await waitFor(() => {
      expect(onStopAgent).toHaveBeenCalledWith('agent:conv:worker-1')
    })
  })

  it('(7) atualiza ao vivo e quando o agente sai da cena mostra estado "não está mais na cena"', () => {
    const initialEvents: ConversationActivityEvent[] = [
      {
        eventId: 'ev-turn-1',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        turnId: 'turn-1',
        agentId: MAIN_AGENT_ID,
        summary: 'Turno 1',
      },
      {
        eventId: 'ev-sub-1',
        cursor: '2',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        turnId: 'turn-1',
        agentId: 'agent:conv:temp-sub',
        agentName: 'Temporário',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'read_file',
        summary: 'Tarefa inicial',
        payload: { subagent_type: 'Explore' },
      },
    ]

    const { rerender } = render(
      <SceneView events={initialEvents} running={true} />,
    )

    const filesSlot = stationSlot('files', 0)
    fireEvent.click(
      screen.getByTestId('scene-canvas'),
      logicalToClient(filesSlot.x, filesSlot.y - 10),
    )

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByTestId('scene-agent-modal-name').textContent).toBe(
      'Temporário',
    )

    // Novo turno inicia sem o subagente Temporário -> agente saiu da cena
    const nextTurnEvents: ConversationActivityEvent[] = [
      ...initialEvents,
      {
        eventId: 'ev-turn-2',
        cursor: '3',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        turnId: 'turn-2',
        agentId: MAIN_AGENT_ID,
        summary: 'Novo turno sem subagente',
      },
    ]

    rerender(<SceneView events={nextTurnEvents} running={true} />)

    const missingNotice = within(screen.getByRole('dialog')).getByTestId(
      'scene-agent-modal-missing',
    )
    expect(missingNotice.textContent).toMatch(/não está mais na cena/i)
  })

  it('(8) ponto único displayName/tag e desenho do retrato em canvas funcionam com overrides e contexto 2D mockado', () => {
    const identity = resolveAgentModalIdentity(
      {
        id: 'agent:conv:claude-1',
        isMain: false,
        name: 'Arquiteto',
        subagentType: 'claude',
      },
      {
        displayName: 'Bento (Arquiteto)',
        tag: 'claude-1',
      },
    )
    expect(identity).toEqual({
      displayName: 'Bento (Arquiteto)',
      tag: 'claude-1',
    })

    const fillRect = vi.fn()
    const clearRect = vi.fn()
    const drawImage = vi.fn()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      () =>
        ({
          fillRect,
          clearRect,
          drawImage,
          imageSmoothingEnabled: true,
        }) as unknown as ReturnType<HTMLCanvasElement['getContext']>,
    )

    const scene: SceneState = {
      turnId: 't-1',
      live: true,
      waitingUser: false,
      lightsOn: true,
      alarmEventId: null,
      agents: [
        {
          id: MAIN_AGENT_ID,
          isMain: true,
          name: 'Orin',
          parentId: null,
          depth: 0,
          station: 'terminal',
          pose: 'think',
          status: 'thinking',
          statusLabel: 'Pensando',
          task: 'Planejar arquitetura',
          currentTool: null,
          lastThought: 'Pensando na solução.',
          lastSpeech: 'Vou planejar.',
          bubble: null,
          activities: [],
          durationLabel: '1 min',
        },
      ],
      transfers: [],
      effects: [],
      pending: [],
    }

    render(
      <SceneAgentModal
        agentId={MAIN_AGENT_ID}
        scene={scene}
        onClose={vi.fn()}
        displayName="Orin Mestre"
        tag="orin-main"
      />,
    )

    expect(screen.getByTestId('scene-agent-modal-name').textContent).toBe(
      'Orin Mestre',
    )
    expect(screen.getByTestId('scene-agent-modal-tag').textContent).toBe(
      'orin-main',
    )
    const portrait = screen.getByTestId('scene-agent-modal-portrait')
    expect(portrait.getAttribute('data-frame')).toBe('orin.think')
    expect(clearRect).toHaveBeenCalled()
    expect(drawImage.mock.calls.length + fillRect.mock.calls.length).toBeGreaterThan(0)
  })
})
