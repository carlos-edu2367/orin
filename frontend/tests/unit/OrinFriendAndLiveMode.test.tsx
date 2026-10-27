import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  Composer,
} from '../../src/features/conversations/Composer';
import {
  WorkspaceNavigation,
} from '../../src/features/projects/WorkspaceNavigation';
import {
  MediaModelsSetting,
} from '../../src/features/providers/MediaModelsSetting';
import {
  FriendSoulDrawer,
} from '../../src/features/conversations/FriendSoulDrawer';
import {
  LiveVoiceStage,
  LONG_FORM_MIC_CONSTRAINTS,
  LIVE_VOICE_DUCKING_MULTIPLIER,
  LIVE_VOICE_DUCKING_MIN_SPEECH_MS,
  LIVE_VOICE_DUCKING_TAIL_MS,
  computeEchoSimilarity,
  isEchoOfRecentTts,
  shouldTriggerDuckingBargeIn,
} from '../../src/features/conversations/LiveVoiceStage';
import {
  CodeSessionLiveCard,
} from '../../src/features/conversations/CodeSessionLiveCard';
import type {
  FriendMemoryEntry,
  FriendPersonaState,
} from '../../src/api/conversations';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SAMPLE_PERSONA: FriendPersonaState = {
  traits: {
    warmth: 0.72,
    humor: 0.58,
    sarcasm: 0.25,
    formality: 0.30,
    verbosity: 0.40,
    candor: 0.75,
    energy: 0.60,
  },
  affinity_score: 68.5,
  bond_stage: 'close_friend',
  style_notes: ['Prefere respostas diretas sem rodeios corporativos', 'Curte analogias de engenharia'],
  current_mood: 'animado e focado',
  soul_markdown_path: 'C:/Users/User/.orin/friend/SOUL.md',
  updated_at: '2026-10-27T15:00:00Z',
};

const SAMPLE_MEMORIES: FriendMemoryEntry[] = [
  {
    id: 'mem-1',
    kind: 'inside_joke',
    content: 'Nunca confie num regex escrito às 3h da manhã',
    importance: 0.9,
    emotion_tag: 'humor',
    created_at: '2026-10-25T10:00:00Z',
  },
  {
    id: 'mem-2',
    kind: 'milestone',
    content: 'Lançou o Orin Code v2 em produção sem downtime',
    importance: 0.95,
    emotion_tag: 'orgulho',
    created_at: '2026-10-26T18:30:00Z',
  },
];

describe('1. WorkspaceNavigation — Orin Friend Fixado', () => {
  it('renderiza o item fixado "◈ Orin Friend" no topo da barra lateral e aciona onOpenOrinFriend', () => {
    const onOpenOrinFriend = vi.fn();

    render(
      <MemoryRouter>
        <WorkspaceNavigation
          onOpenOrinFriend={onOpenOrinFriend}
          isOrinFriendActive={true}
          friendBondStage="close_friend"
        />
      </MemoryRouter>,
    );

    const friendButton = screen.getByTestId('sidebar-orin-friend-button');
    expect(friendButton).toBeTruthy();
    expect(friendButton.textContent).toContain('Orin Friend');

    fireEvent.click(friendButton);
    expect(onOpenOrinFriend).toHaveBeenCalledTimes(1);
  });
});

describe('2. FriendSoulDrawer — SOUL.md, 7 Traços Evolutivos (Candor >= 0.50) e Memórias Afetivas', () => {
  it('exibe bond_stage, affinity_score, current_mood, sincroniza SOUL.md, trava candor >= 0.50 e exclui memórias', async () => {
    const onUpdatePersona = vi.fn().mockResolvedValue(SAMPLE_PERSONA);
    const onSyncSoul = vi.fn().mockResolvedValue(SAMPLE_PERSONA);
    const onDeleteMemory = vi.fn().mockResolvedValue(undefined);

    render(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={SAMPLE_MEMORIES}
        onClose={vi.fn()}
        onUpdatePersona={onUpdatePersona}
        onSyncSoul={onSyncSoul}
        onDeleteMemory={onDeleteMemory}
      />,
    );

    expect(screen.getByTestId('friend-soul-drawer')).toBeTruthy();
    expect(screen.getByText(/Amigo Próximo/i)).toBeTruthy();
    expect(screen.getByText(/69%|68\.5%/i)).toBeTruthy();
    expect(screen.getByText(/animado e focado/i)).toBeTruthy();

    // Tenta reduzir candor abaixo do piso de 0.50 (deve travar em 0.50)
    const candorSlider = screen.getByTestId('trait-slider-candor') as HTMLInputElement;
    expect(candorSlider).toBeTruthy();
    fireEvent.change(candorSlider, { target: { value: '0.20' } });

    const saveBtn = screen.getByTestId('friend-soul-save-button');
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onUpdatePersona).toHaveBeenCalledTimes(1);
    });
    const payload = onUpdatePersona.mock.calls[0][0];
    expect(payload.traits.candor).toBeGreaterThanOrEqual(0.5);

    // Sincronizar SOUL.md
    fireEvent.click(screen.getByTestId('friend-soul-sync-button'));
    await waitFor(() => {
      expect(onSyncSoul).toHaveBeenCalledTimes(1);
    });

    // Excluir memória afetiva
    expect(screen.getByText(/Nunca confie num regex escrito às 3h da manhã/i)).toBeTruthy();
    const deleteMemBtn = screen.getByTestId('delete-friend-memory-mem-1');
    fireEvent.click(deleteMemBtn);
    await waitFor(() => {
      expect(onDeleteMemory).toHaveBeenCalledWith('mem-1');
    });
  });
});

describe('3. Composer & LiveVoiceStage — 5 Estados, 4 Barreiras Anti-Eco e Barge-in <80ms', () => {
  it('renderiza botão ((•)) LIVE no Composer nos modos friend, chat e code e responde ao atalho Alt+L', () => {
    const onToggleLiveVoice = vi.fn();

    render(
      <Composer
        onSend={vi.fn()}
        mode="friend"
        onModeChange={vi.fn()}
        isLiveVoiceActive={false}
        onToggleLiveVoice={onToggleLiveVoice}
      />,
    );

    const liveBtn = screen.getByTestId('composer-live-voice-toggle');
    expect(liveBtn).toBeTruthy();
    expect(liveBtn.textContent).toContain('LIVE');

    fireEvent.click(liveBtn);
    expect(onToggleLiveVoice).toHaveBeenCalledTimes(1);

    // Atalho Alt+L global
    fireEvent.keyDown(window, { key: 'l', altKey: true });
    expect(onToggleLiveVoice).toHaveBeenCalledTimes(2);
  });

  it('valida as 4 barreiras Anti-Eco (constraints WebRTC, VAD Ducking 2.8x + 220ms + 250ms tail, similaridade lexical >= 0.65 e PTT)', () => {
    // Barreira 1: WebRTC AudioConstraints estritos
    expect(LONG_FORM_MIC_CONSTRAINTS).toEqual({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
      channelCount: 1,
      sampleRate: 24000,
    });

    // Barreira 2: Ducking de sensibilidade do VAD (2.8x limiar + >=220ms fala contínua + 250ms cauda)
    expect(LIVE_VOICE_DUCKING_MULTIPLIER).toBe(2.8);
    expect(LIVE_VOICE_DUCKING_MIN_SPEECH_MS).toBe(220);
    expect(LIVE_VOICE_DUCKING_TAIL_MS).toBe(250);

    // Enquanto o assistente fala ou está dentro da cauda de 250ms:
    // Energia abaixo de 2.8x do limiar base NÃO dispara barge-in
    expect(
      shouldTriggerDuckingBargeIn({
        rmsEnergy: 0.05,
        baseThreshold: 0.025, // 2.8x = 0.07
        sustainedSpeechMs: 300,
        isAssistantSpeaking: true,
        msSinceLastTtsChunk: 50,
      }),
    ).toBe(false);

    // Energia acima de 2.8x mas duração < 220ms NÃO dispara barge-in (pico transitório/eco)
    expect(
      shouldTriggerDuckingBargeIn({
        rmsEnergy: 0.09,
        baseThreshold: 0.025,
        sustainedSpeechMs: 140,
        isAssistantSpeaking: true,
        msSinceLastTtsChunk: 50,
      }),
    ).toBe(false);

    // Energia >= 2.8x por >= 220ms contínuos dispara barge-in legítimo!
    expect(
      shouldTriggerDuckingBargeIn({
        rmsEnergy: 0.09,
        baseThreshold: 0.025,
        sustainedSpeechMs: 240,
        isAssistantSpeaking: true,
        msSinceLastTtsChunk: 50,
      }),
    ).toBe(true);

    // Barreira 3: Gate de Similaridade Lexical cliente >= 0.65
    const recentTts = ['Claro, já mandei o Orin Code rodar a suíte de testes agora mesmo.'];
    expect(
      isEchoOfRecentTts('claro ja mandei o orin code rodar a suite de testes', recentTts),
    ).toBe(true);
    expect(
      computeEchoSimilarity('claro ja mandei o orin code rodar a suite de testes', recentTts[0]),
    ).toBeGreaterThanOrEqual(0.65);
    expect(
      isEchoOfRecentTts('Na verdade, pare os testes e crie outra branch primeiro.', recentTts),
    ).toBe(false);
  });

  it('renderiza LiveVoiceStage nos 5 estados, destaca legenda palavra por palavra, respeita prefers-reduced-motion e aciona barge-in e Push-to-Talk', () => {
    const onBargeIn = vi.fn();
    const onClose = vi.fn();
    const onTogglePtt = vi.fn();

    render(
      <LiveVoiceStage
        isOpen={true}
        voiceState="speaking"
        spokenText="Analisando os testes do módulo de autenticação agora."
        wordTimings={[
          { word: 'Analisando', start_ms: 0, end_ms: 350, char_start: 0, char_end: 10 },
          { word: 'os', start_ms: 350, end_ms: 480, char_start: 11, char_end: 13 },
          { word: 'testes', start_ms: 480, end_ms: 820, char_start: 14, char_end: 20 },
        ]}
        playbackElapsedMs={520}
        workingStatusText="Orin Code: rodando pytest (3/4 concluídas)"
        isPushToTalkMode={true}
        isPushToTalkActive={false}
        reducedMotion={true}
        onBargeIn={onBargeIn}
        onTogglePushToTalkMode={onTogglePtt}
        onClose={onClose}
      />,
    );

    const stage = screen.getByTestId('live-voice-stage');
    expect(stage).toBeTruthy();
    expect(stage.getAttribute('data-voice-state')).toBe('speaking');
    expect(stage.getAttribute('data-reduced-motion')).toBe('true');

    // Palavra ativa destacada pela sincronia de wordTimings (520ms -> 'testes')
    const activeWord = screen.getByTestId('live-caption-active-word');
    expect(activeWord.textContent).toContain('testes');

    // Botão de barge-in imediato
    const bargeInBtn = screen.getByTestId('live-voice-barge-in-button');
    fireEvent.click(bargeInBtn);
    expect(onBargeIn).toHaveBeenCalledWith(expect.any(Number));

    // Botão Push-to-Talk (Barreira 4)
    const pttBtn = screen.getByTestId('live-voice-ptt-button');
    expect(pttBtn).toBeTruthy();
  });
});

describe('4. CodeSessionLiveCard — Card Compacto de Delegação para o Orin Code', () => {
  it('exibe progresso amigável do TodoWrite, tarefa ativa e botão para abrir no Orin Code', () => {
    const onOpenCodeSession = vi.fn();

    render(
      <CodeSessionLiveCard
        codeConversationId="conv-code-99"
        goal="Corrigir bug de reconexão do WebSocket no modo Live"
        status="running"
        completedTodoCount={3}
        totalTodoCount={4}
        activeTaskTitle="Rodando suíte de testes unitários (pytest)"
        modifiedFilesCount={2}
        examinedFilesCount={5}
        friendlyMilestone="3/4 concluídas — Reta final!"
        onOpenCodeSession={onOpenCodeSession}
      />,
    );

    const card = screen.getByTestId('code-session-live-card');
    expect(card).toBeTruthy();
    expect(card.textContent).toContain('3/4 concluídas — Reta final!');
    expect(card.textContent).toContain('Rodando suíte de testes unitários (pytest)');

    const openBtn = screen.getByTestId('open-in-orin-code-button');
    fireEvent.click(openBtn);
    expect(onOpenCodeSession).toHaveBeenCalledWith('conv-code-99');
  });
});

describe('5. MediaModelsSetting — Seletores Independentes de STT e TTS + VoiceHardwareGovernor', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
        const url = typeof input === 'string' ? input : input.toString();
        const method = init?.method ?? 'GET';

        if (url.endsWith('/v1/settings/media-models') && method === 'GET') {
          return new Response(
            JSON.stringify({
              image: { provider: null, model_id: null, mode: 'automatic' },
              audio: { provider: 'local_sidecar', model_id: 'chatterbox-pt-br', mode: 'manual' },
              stt: { provider: 'local_sidecar', model_id: 'faster-whisper-large-v3-turbo', mode: 'manual' },
              video: { provider: null, model_id: null, mode: 'automatic' },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }

        if (url.endsWith('/v1/live-voice/hardware') && method === 'GET') {
          return new Response(
            JSON.stringify({
              gpu_available: true,
              gpu_name: 'NVIDIA RTX 4070',
              free_vram_gb: 9.4,
              free_ram_gb: 22.0,
              cpu_Idle_percent: 82.0,
              recommended_tts_provider: 'local_sidecar',
              recommended_tts_model: 'chatterbox-pt-br',
              recommended_stt_provider: 'local_sidecar',
              recommended_stt_model: 'faster-whisper-large-v3-turbo',
              reason: 'GPU com 9.4 GB de VRAM livre — modelos locais de alta fidelidade habilitados.',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }

        if (url.endsWith('/v1/settings/media-models/stt') && method === 'PUT') {
          const body = JSON.parse(String(init?.body ?? '{}'));
          return new Response(
            JSON.stringify({
              modality: 'stt',
              provider: body.provider,
              model_id: body.model_id,
              mode: body.provider === null ? 'automatic' : 'manual',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }

        if (url.endsWith('/v1/settings/media-models') && method === 'PUT') {
          const body = JSON.parse(String(init?.body ?? '{}'));
          return new Response(JSON.stringify(body), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        return new Response('Not Found', { status: 404 });
      }),
    );
  });

  it('renderiza seletores independentes de Áudio e Voz (TTS) e Ouvido e Transcrição (STT) mais diagnóstico do VoiceHardwareGovernor', async () => {
    render(<MediaModelsSetting />);

    const audioRow = await screen.findByTestId('media-model-row-audio');
    const sttRow = await screen.findByTestId('media-model-row-stt');
    expect(audioRow).toBeTruthy();
    expect(sttRow).toBeTruthy();

    const governorCard = await screen.findByTestId('voice-hardware-governor-card');
    expect(governorCard).toBeTruthy();
    expect(governorCard.textContent).toContain('9.4 GB');

    const audioSelect = (await screen.findByLabelText('Modelo padrão de áudio')) as HTMLSelectElement;
    const sttSelect = (await screen.findByLabelText('Modelo padrão de transcrição (STT)')) as HTMLSelectElement;

    expect(audioSelect.value).toBe('local_sidecar:chatterbox-pt-br');
    expect(sttSelect.value).toBe('local_sidecar:faster-whisper-large-v3-turbo');

    fireEvent.change(sttSelect, { target: { value: 'groq:whisper-large-v3-turbo' } });
    fireEvent.click(screen.getByTestId('save-media-models-button'));

    await waitFor(() => {
      const putCall = vi
        .mocked(fetch)
        .mock.calls.find(
          ([input, init]) =>
            String(input).endsWith('/v1/settings/media-models') && init?.method === 'PUT',
        );
      expect(putCall).toBeDefined();
      const sentBody = JSON.parse(String(putCall?.[1]?.body ?? '{}'));
      expect(sentBody.stt).toEqual({
        provider: 'groq',
        model_id: 'whisper-large-v3-turbo',
        mode: 'manual',
      });
    });
  });

  it('abre LiveVoiceQuickSettings dentro do LiveVoiceStage, aplica preset em 1 clique e testa amostra de voz PT-BR', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
        const url = typeof input === 'string' ? input : input.toString();
        const method = init?.method ?? 'GET';

        if (url.endsWith('/v1/settings/media-models') && method === 'GET') {
          return new Response(
            JSON.stringify({
              image: { provider: null, model_id: null, mode: 'automatic' },
              audio: { provider: 'local_sidecar', model_id: 'chatterbox-pt-br', mode: 'manual' },
              stt: { provider: 'local_sidecar', model_id: 'faster-whisper-large-v3-turbo', mode: 'manual' },
              video: { provider: null, model_id: null, mode: 'automatic' },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }

        if ((url.endsWith('/v1/live-voice/hardware') || url.endsWith('/v1/live-voice/hardware-status')) && method === 'GET') {
          return new Response(
            JSON.stringify({
              gpu_available: true,
              gpu_name: 'NVIDIA GeForce RTX 4060',
              free_vram_gb: 8.0,
              free_ram_gb: 16.0,
              recommended_tts_provider: 'local_sidecar',
              recommended_tts_model: 'chatterbox-pt-br',
              recommended_stt_provider: 'local_sidecar',
              recommended_stt_model: 'faster-whisper-large-v3-turbo',
              reason: 'RTX 4060 8GB detectada — perfil local PT-BR expressivo ativo.',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }

        if (url.includes('/v1/settings/media-models/') && method === 'PUT') {
          const modality = url.split('/').pop() ?? 'audio';
          const body = JSON.parse(String(init?.body ?? '{}'));
          return new Response(
            JSON.stringify({
              modality,
              provider: body.provider,
              model_id: body.model_id,
              mode: body.provider === null ? 'automatic' : 'manual',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }

        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );

    render(<LiveVoiceStage isOpen={true} onClose={vi.fn()} />);

    const openSettingsBtn = screen.getByTestId('live-voice-open-settings-button');
    fireEvent.click(openSettingsBtn);

    const quickModal = await screen.findByTestId('live-voice-quick-settings');
    expect(quickModal).toBeTruthy();

    const hwBanner = await screen.findByTestId('live-quick-hardware-banner');
    expect(hwBanner.textContent).toContain('RTX 4060');

    // Aplica preset Híbrido Ultra-Rápido em 1 clique
    const hybridPresetBtn = screen.getByTestId('live-preset-card-hybrid_fast');
    fireEvent.click(hybridPresetBtn);

    await waitFor(() => {
      const sttSelect = screen.getByTestId('live-quick-stt-select') as HTMLSelectElement;
      const ttsSelect = screen.getByTestId('live-quick-tts-select') as HTMLSelectElement;
      expect(ttsSelect.value).toBe('local_sidecar:chatterbox-pt-br');
      expect(sttSelect.value).toBe('groq:whisper-large-v3-turbo');
    });

    // Testa amostra de voz PT-BR
    const testVoiceBtn = screen.getByTestId('live-quick-test-voice-button');
    fireEvent.click(testVoiceBtn);
    const previewStatus = await screen.findByTestId('live-quick-voice-preview-status');
    expect(previewStatus.textContent).toContain('Olá!');
  });
});
