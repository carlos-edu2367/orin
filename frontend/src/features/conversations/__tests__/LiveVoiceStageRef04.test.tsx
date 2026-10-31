import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  LiveVoiceStage,
  stateLabel,
  type LiveVoiceTranscriptItem,
} from '../LiveVoiceStage'
import type { DelegatedCodeSessionInfo, TodoItem } from '../activityTypes'
import { MemoryRouter } from 'react-router-dom'
import type { ApiClient } from '../../../api/client'
import * as LiveVoiceApi from '../../../api/liveVoice'
import { GlobalWakeWordListener } from '../live/GlobalWakeWordListener'
import { LIVE_FRAME_SAMPLES, MicCapture } from '../live/liveAudio'
import { setLiveVoiceStageOpen } from '../live/wakeWord'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('LiveVoiceStage — Fase 4 (ref-04.png: Palco Friend / Ao vivo)', () => {
  it('mantém stateLabel retrocompatível e higieniza userNickname em listening (Red-Team)', () => {
    expect(stateLabel('listening')).toBe('Estou ouvindo')
    expect(stateLabel('listening', 'Orin', null, '')).toBe('Estou ouvindo')
    expect(stateLabel('listening', 'Orin', null, '   ')).toBe('Estou ouvindo')
    expect(stateLabel('listening', 'Orin', null, 'Carlos')).toBe('Estou ouvindo, Carlos.')

    // Red-Team: caracteres proibidos (<>{}`), pontuação final duplicada e > 40 chars
    expect(stateLabel('listening', 'Orin', null, '  <Carlos>{}`...!?  ')).toBe('Estou ouvindo, Carlos.')
    expect(stateLabel('listening', 'Orin', null, '<>{}`')).toBe('Estou ouvindo')

    const longNick = 'A'.repeat(55)
    const labelWithLongNick = stateLabel('listening', 'Orin', null, longNick)
    expect(labelWithLongNick).toBe(`Estou ouvindo, ${'A'.repeat(40)}.`)
  })

  it('renderiza header com breadcrumb Friend / Ao vivo, pill de status do microfone e controles preservados', () => {
    const onClose = vi.fn()
    const onOpenPersonalize = vi.fn()

    render(
      <LiveVoiceStage
        isOpen={true}
        conversationId="conv-ref04"
        companionName="Orin"
        userNickname="Carlos"
        phaseOverride="listening"
        reducedMotion={true}
        onOpenPersonalize={onOpenPersonalize}
        onClose={onClose}
      />,
    )

    const breadcrumb = screen.getByTestId('live-voice-breadcrumb')
    expect(breadcrumb.textContent).toContain('Friend')
    expect(breadcrumb.textContent).toContain('/')
    expect(within(breadcrumb).getByTestId('live-voice-companion-name').textContent).toBe('Orin')
    expect(breadcrumb.textContent).toContain('Ao vivo')

    const micPill = screen.getByTestId('live-voice-mic-status-pill')
    expect(micPill.textContent).toContain('Microfone ativo')

    // Ao silenciar no dock, o pill do topo reflete "Microfone silenciado"
    const muteBtn = screen.getByTestId('live-voice-mute-button')
    expect(muteBtn.textContent).toContain('Silenciar microfone')
    fireEvent.click(muteBtn)
    expect(muteBtn.getAttribute('aria-pressed')).toBe('true')
    expect(muteBtn.textContent).toContain('Ativar microfone')
    expect(micPill.textContent).toContain('Microfone silenciado')

    // Gatilhos existentes preservados
    expect(screen.getByTestId('live-voice-active-profile-pill')).toBeTruthy()
    expect(screen.getByTestId('live-voice-open-settings-button')).toBeTruthy()
    expect(screen.getByTestId('live-voice-close-button')).toBeTruthy()
  })

  it('renderiza mascote soft3d + LiveOrb no palco central, headline personalizado, waveform de 31 barras e toggle de legendas', () => {
    render(
      <LiveVoiceStage
        isOpen={true}
        conversationId="conv-ref04"
        companionName="Orin"
        userNickname="Carlos"
        phaseOverride="listening"
        reducedMotion={true}
        onClose={vi.fn()}
      />,
    )

    const orbContainer = screen.getByTestId('live-voice-orb')
    const mascotInOrb = within(orbContainer).getByTestId('orin-mascot')
    expect(mascotInOrb.getAttribute('data-variant')).toBe('soft3d')
    expect(orbContainer.querySelector('.live-orb')).toBeTruthy()

    expect(screen.getByTestId('live-voice-state-label').textContent).toBe('Estou ouvindo, Carlos.')

    const waveform = screen.getByTestId('live-voice-waveform')
    const bars = waveform.querySelectorAll('.live-waveform__bar')
    expect(bars.length).toBe(31)

    const edgeHeight = Number.parseFloat((bars[0] as HTMLElement).style.height)
    const centerHeight = Number.parseFloat((bars[15] as HTMLElement).style.height)
    expect(centerHeight).toBeGreaterThan(edgeHeight)

    const captions = screen.getByTestId('live-voice-captions')
    expect(captions.getAttribute('data-captions-enabled')).toBe('true')
    expect(captions.textContent).toContain('Oi, Carlos!')
    expect(captions.textContent).toContain('Pode falar naturalmente e me interromper.')

    const captionsToggle = screen.getByTestId('live-voice-captions-toggle')
    expect(captionsToggle.getAttribute('aria-pressed')).toBe('true')
    expect(captionsToggle.textContent).toContain('Legendas ao vivo')

    fireEvent.click(captionsToggle)
    expect(captionsToggle.getAttribute('aria-pressed')).toBe('false')
    expect(captions.getAttribute('data-captions-enabled')).toBe('false')
    expect(captions.textContent).toContain('Oi, Carlos!')
    expect(captions.textContent).toContain('Pode falar naturalmente e me interromper.')

    // Quando não há falas nem tarefas delegadas, exibe estado vazio limpo e oculta o card de tarefa
    expect(screen.getByTestId('live-voice-transcript-empty').textContent).toBe(
      'As falas desta conversa aparecem aqui.',
    )
    expect(screen.queryByTestId('live-voice-task-card')).toBeNull()
  })

  it('renderiza coluna direita "Nesta conversa" com transcrição sanitizada e card de tarefa com botão Abrir no Code', () => {
    const onOpenCodeSession = vi.fn()
    const transcriptItems: LiveVoiceTranscriptItem[] = [
      {
        id: 't-1',
        role: 'user',
        content: 'Como está a página da Auryly?',
        timestamp: '14:26',
      },
      {
        id: 't-2',
        role: 'assistant',
        content:
          '<speak>A prévia já está pronta.</speak> ```ts\nconst secret = 42;\n``` **Quer dar uma olhada?** ' +
          'X'.repeat(200),
        timestamp: '14:27',
      },
    ]
    const todos: TodoItem[] = [
      { id: '1', content: 'Estrutura da página', status: 'completed' },
      { id: '2', content: 'Seções principais', status: 'completed' },
      { id: '3', content: 'Revisão de conteúdo', status: 'completed' },
      { id: '4', content: 'Publicação', status: 'pending' },
    ]
    const delegatedCodeSession: DelegatedCodeSessionInfo = {
      code_conversation_id: 'code-conv-77',
      goal: 'Página da Auryly',
      status: 'running',
      completed_todo_count: 3,
      total_todo_count: 4,
    }

    render(
      <LiveVoiceStage
        isOpen={true}
        companionName="Orin"
        userNickname="Carlos"
        phaseOverride="listening"
        reducedMotion={true}
        transcriptItems={transcriptItems}
        todos={todos}
        delegatedCodeSession={delegatedCodeSession}
        onOpenCodeSession={onOpenCodeSession}
        onClose={vi.fn()}
      />,
    )

    const sidePanel = screen.getByTestId('live-voice-side-panel')
    expect(sidePanel).toBeTruthy()

    const transcriptList = within(sidePanel).getByTestId('live-voice-transcript-list')
    expect(transcriptList.textContent).toContain('Nesta conversa')
    expect(transcriptList.textContent).toContain('Você')
    expect(transcriptList.textContent).toContain('Como está a página da Auryly?')
    expect(transcriptList.textContent).toContain('A prévia já está pronta.')
    expect(transcriptList.textContent).not.toContain('<speak>')
    expect(transcriptList.textContent).not.toContain('const secret')
    expect(transcriptList.textContent).toContain('…')

    const taskCard = within(sidePanel).getByTestId('live-voice-task-card')
    expect(taskCard.textContent).toContain('Página da Auryly')
    expect(taskCard.textContent).toContain('3 de 4 etapas concluídas')
    expect(taskCard.textContent).toContain('Estrutura da página')
    expect(taskCard.textContent).toContain('Publicação')

    const progressBar = within(taskCard).getByTestId('live-voice-task-progress-bar')
    expect(progressBar.getAttribute('aria-valuenow')).toBe('75')

    const openCodeBtn = within(taskCard).getByTestId('live-voice-open-in-code')
    expect(openCodeBtn.textContent).toContain('Abrir no Code')
    fireEvent.click(openCodeBtn)
    expect(onOpenCodeSession).toHaveBeenCalledWith('code-conv-77')
  })

  it('protege contra divisão por zero quando total_todo_count é 0 e não falha sem onOpenCodeSession (Red-Team)', () => {
    render(
      <LiveVoiceStage
        isOpen={true}
        companionName="Orin"
        phaseOverride="listening"
        reducedMotion={true}
        todos={[]}
        delegatedCodeSession={{
          code_conversation_id: 'code-empty',
          goal: ' Setup inicial ',
          status: 'running',
          completed_todo_count: 0,
          total_todo_count: 0,
        }}
        onClose={vi.fn()}
      />,
    )

    const taskCard = screen.getByTestId('live-voice-task-card')
    expect(taskCard.textContent).toContain('0 de 0 etapas concluídas')
    const progressBar = within(taskCard).getByTestId('live-voice-task-progress-bar')
    expect(progressBar.getAttribute('aria-valuenow')).toBe('0')

    expect(() => {
      fireEvent.click(within(taskCard).getByTestId('live-voice-open-in-code'))
    }).not.toThrow()
  })

  it('enumera dispositivos audiooutput no seletor de saída e mantém fallback seguro se mediaDevices falhar ou não existir (Red-Team)', async () => {
    const onClose = vi.fn()
    const addEventListener = vi.fn()
    const removeEventListener = vi.fn()
    const enumerateDevices = vi.fn().mockResolvedValue([
      { kind: 'audioinput', deviceId: 'mic-1', label: 'Microfone USB' },
      { kind: 'audiooutput', deviceId: 'spk-mac', label: 'Alto-falantes (MacBook)' },
      { kind: 'audiooutput', deviceId: 'spk-airpods', label: 'AirPods Pro' },
    ])

    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        enumerateDevices,
        addEventListener,
        removeEventListener,
      },
      configurable: true,
      writable: true,
    })

    const { unmount } = render(
      <LiveVoiceStage
        isOpen={true}
        companionName="Orin"
        phaseOverride="listening"
        reducedMotion={true}
        onClose={onClose}
      />,
    )

    const picker = screen.getByTestId('live-voice-speaker-picker')
    await waitFor(() => {
      expect(picker.textContent).toContain('Alto-falantes (MacBook)')
      expect(picker.textContent).toContain('AirPods Pro')
    })
    expect(addEventListener).toHaveBeenCalledWith('devicechange', expect.any(Function))

    // Botão encerrar no dock chama onClose
    const endBtn = screen.getByTestId('live-voice-end-call-button')
    expect(endBtn.textContent).toContain('Encerrar')
    fireEvent.click(endBtn)
    expect(onClose).toHaveBeenCalledTimes(1)

    // Controles de compatibilidade permanecem no dock
    expect(screen.getByTestId('live-mic-meter')).toBeTruthy()
    expect(screen.getByTestId('live-voice-barge-in-button')).toBeTruthy()
    expect(screen.getByTestId('live-voice-ptt-button')).toBeTruthy()

    unmount()
    expect(removeEventListener).toHaveBeenCalledWith('devicechange', expect.any(Function))

    // Red-Team: enumerateDevices rejeitando com NotAllowedError
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        enumerateDevices: vi.fn().mockRejectedValue(new DOMException('Permission denied', 'NotAllowedError')),
      },
      configurable: true,
      writable: true,
    })

    render(
      <LiveVoiceStage
        isOpen={true}
        companionName="Orin"
        phaseOverride="listening"
        reducedMotion={true}
        onClose={vi.fn()}
      />,
    )

    const fallbackPicker = screen.getByTestId('live-voice-speaker-picker')
    expect(fallbackPicker.textContent).toContain('Alto-falantes (Padrão)')
  })

  it('GlobalWakeWordListener em standby desativa headphoneMode (AGC), ignora cliques curtos de 2 frames cercados de silêncio e só chama wake-check com >= 6 frames vocais (RMS >= 0.020 e pico >= 0.025)', async () => {
    window.localStorage.removeItem('orin.live-voice.prefs.v1')
    setLiveVoiceStageOpen(false)

    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [], getAudioTracks: () => [] }),
        enumerateDevices: vi.fn().mockResolvedValue([]),
      },
      configurable: true,
      writable: true,
    })

    let capturedOnFrame: ((frame: Int16Array, rms: number) => void) | undefined
    let capturedHeadphoneMode: boolean | undefined
    vi.spyOn(MicCapture, 'open').mockImplementation(async (opts) => {
      capturedOnFrame = opts?.onFrame
      capturedHeadphoneMode = opts?.headphoneMode
      return {
        level: 0,
        deviceLabel: 'Mic Standby',
        close: vi.fn(),
      } as unknown as MicCapture
    })

    vi.spyOn(LiveVoiceApi, 'getLiveVoiceCapabilities').mockResolvedValue({
      ...LiveVoiceApi.UNAVAILABLE_CAPABILITIES,
      stt: {
        ...LiveVoiceApi.UNAVAILABLE_CAPABILITIES.stt,
        local_sidecar: {
          available: true,
          reason: '',
          modelId: 'faster-whisper-large-v3-turbo',
          models: [],
          voices: [],
        },
      },
      recommendedStt: 'local_sidecar',
    })

    const checkWakeWordSpy = vi.spyOn(LiveVoiceApi, 'checkWakeWord').mockResolvedValue({
      matched: false,
      wake_variant: '',
      command_remainder: '',
      confidence: 0,
      transcript: '',
    })

    const fakeClient = {
      request: vi.fn().mockResolvedValue(null),
      createMutationIntent: () => ({ idempotencyKey: 'k-wake-standby' }),
    } as unknown as ApiClient

    try {
      render(
        <MemoryRouter initialEntries={['/']}>
          <GlobalWakeWordListener client={fakeClient} />
        </MemoryRouter>,
      )

      await waitFor(() => expect(capturedOnFrame).toBeDefined())
      expect(capturedHeadphoneMode).toBe(false)

      const silenceFrame = new Int16Array(LIVE_FRAME_SAMPLES).fill(5) // RMS ~ 0.00015
      const clickFrame = new Int16Array(LIVE_FRAME_SAMPLES).fill(2000) // RMS ~ 0.061 (>= 0.025)
      const lowNoiseFrame = new Int16Array(LIVE_FRAME_SAMPLES).fill(500) // RMS ~ 0.015 (< 0.020, mas > threshold 0.0145)

      // 1. Pre-roll de silêncio (10 frames) + clique curto de 2 frames (64ms) + 20 frames de silêncio
      act(() => {
        for (let i = 0; i < 10; i += 1) {
          capturedOnFrame?.(silenceFrame, 0.00015)
        }
        for (let i = 0; i < 2; i += 1) {
          capturedOnFrame?.(clickFrame, 0.061)
        }
        for (let i = 0; i < 20; i += 1) {
          capturedOnFrame?.(silenceFrame, 0.00015)
        }
      })
      expect(checkWakeWordSpy).not.toHaveBeenCalled()

      // 2. Mesmo se o EnergyVad abrir utterance com 2 frames de clique + 4 frames de ruído baixo (25+ frames totais com pre-roll/post-roll),
      // inspectSpeechFrames rejeita pois há apenas 2 frames com frameRms >= 0.020 (< 6).
      act(() => {
        for (let i = 0; i < 10; i += 1) {
          capturedOnFrame?.(silenceFrame, 0.00015)
        }
        for (let i = 0; i < 2; i += 1) {
          capturedOnFrame?.(clickFrame, 0.061)
        }
        for (let i = 0; i < 4; i += 1) {
          capturedOnFrame?.(lowNoiseFrame, 0.016)
        }
        for (let i = 0; i < 20; i += 1) {
          capturedOnFrame?.(silenceFrame, 0.00015)
        }
      })
      expect(checkWakeWordSpy).not.toHaveBeenCalled()

      // 2b. Adversarial: 8 frames com RMS ~0.021 (>= 0.020) mas peakRms < 0.025 (int16 = 700 -> 0.02136) NÃO dispara wake-check
      const borderlineVoicedFrame = new Int16Array(LIVE_FRAME_SAMPLES).fill(700)
      act(() => {
        for (let i = 0; i < 8; i += 1) {
          capturedOnFrame?.(borderlineVoicedFrame, 0.0213)
        }
        for (let i = 0; i < 20; i += 1) {
          capturedOnFrame?.(silenceFrame, 0.00015)
        }
      })
      expect(checkWakeWordSpy).not.toHaveBeenCalled()

      // 3. Fala real com >= 6 frames vocais (8 frames com RMS ~0.061 >= 0.025) seguida de silêncio dispara checkWakeWord
      act(() => {
        for (let i = 0; i < 8; i += 1) {
          capturedOnFrame?.(clickFrame, 0.061)
        }
        for (let i = 0; i < 18; i += 1) {
          capturedOnFrame?.(silenceFrame, 0.00015)
        }
      })

      await waitFor(() => expect(checkWakeWordSpy).toHaveBeenCalledTimes(1))
    } finally {
      setLiveVoiceStageOpen(false)
    }
  })
})
