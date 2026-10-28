import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  stateLabel,
} from '../../src/features/conversations/LiveVoiceStage';
import {
  EnergyVad,
  LIVE_FRAME_SAMPLES,
  DUCKING_MULTIPLIER,
  DUCKING_MIN_SPEECH_MS,
  MicCapture,
  StreamingResampler,
  levelFromRms,
} from '../../src/features/conversations/live/liveAudio';
import { SpeechOutput, textForSpeech } from '../../src/features/conversations/live/liveSpeech';
import {
  DEFAULT_LIVE_VOICE_PREFS,
  loadLiveVoicePrefs,
  normalizeLiveVoicePrefs,
  resolveStt,
  resolveTts,
  ttsSelection,
  voiceLabel,
} from '../../src/features/conversations/live/liveVoicePrefs';
import { UNAVAILABLE_CAPABILITIES } from '../../src/api/liveVoice';
import {
  CodeSessionLiveCard,
} from '../../src/features/conversations/CodeSessionLiveCard';
import {
  sendConversationMessage,
  type FriendMemoryEntry,
  type FriendPersonaState,
  type LiveVoiceServerEvent,
  type LiveVoiceSocketConfig,
} from '../../src/api/conversations';
import type { ApiClient } from '../../src/api/client';
import * as ConversationsApi from '../../src/api/conversations';
import * as ChatPageModule from '../../src/features/conversations/ChatPage';
import { useLiveVoice } from '../../src/features/conversations/live/useLiveVoice';
import { act, renderHook } from '@testing-library/react';

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
  display_name: 'Orin',
  user_nickname: 'Carlos',
  orb_theme: 'aurora',
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

describe('2. FriendSoulDrawer — nome, aparência, personalidade e memórias', () => {
  it('personaliza nome, apelido, tema e preset, mantém o piso de sinceridade, sincroniza SOUL.md e esquece memórias', async () => {
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

    expect(screen.getByTestId('friend-bond-stage').textContent).toBe('Amigo próximo');
    expect(screen.getByTestId('friend-affinity-score').textContent).toContain('69%');
    expect((screen.getByTestId('friend-mood-input') as HTMLInputElement).value).toBe('animado e focado');
    expect((screen.getByTestId('friend-nickname-input') as HTMLInputElement).value).toBe('Carlos');

    // Nada mudou ainda: salvar fica desabilitado.
    expect((screen.getByTestId('save-friend-persona-button') as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByTestId('friend-name-input'), { target: { value: 'Jarvis' } });
    fireEvent.click(screen.getByTestId('friend-theme-ocean'));
    fireEvent.click(screen.getByTestId('friend-preset-animado'));
    expect(screen.getByTestId('friend-preset-animado').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Jarvis');

    // O ajuste fino não deixa a sinceridade passar abaixo do piso 0.50.
    fireEvent.change(screen.getByTestId('friend-trait-slider-candor'), { target: { value: '0.20' } });

    fireEvent.click(screen.getByTestId('save-friend-persona-button'));
    await waitFor(() => expect(onUpdatePersona).toHaveBeenCalledTimes(1));
    const payload = onUpdatePersona.mock.calls[0][0];
    expect(payload.display_name).toBe('Jarvis');
    expect(payload.user_nickname).toBe('Carlos');
    expect(payload.orb_theme).toBe('ocean');
    expect(payload.traits.candor).toBeGreaterThanOrEqual(0.5);
    expect(payload.traits.energy).toBe(0.85);
    expect(await screen.findByText('Jarvis foi atualizado.')).toBeTruthy();

    fireEvent.click(screen.getByTestId('sync-friend-soul-button'));
    await waitFor(() => expect(onSyncSoul).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId('delete-friend-memory-mem-1'));
    await waitFor(() => expect(onDeleteMemory).toHaveBeenCalledWith('mem-1'));
  });

  it('não salva um nome vazio', async () => {
    const onUpdatePersona = vi.fn();
    render(
      <FriendSoulDrawer isOpen persona={SAMPLE_PERSONA} memories={[]} onClose={vi.fn()} onUpdatePersona={onUpdatePersona} onSyncSoul={vi.fn()} onDeleteMemory={vi.fn()} />,
    );
    fireEvent.change(screen.getByTestId('friend-name-input'), { target: { value: '   ' } });
    fireEvent.click(screen.getByTestId('save-friend-persona-button'));
    expect(await screen.findByTestId('friend-save-status')).toBeTruthy();
    expect(screen.getByTestId('friend-save-status').textContent).toContain('Dê um nome');
    expect(onUpdatePersona).not.toHaveBeenCalled();
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

  it('mostra o nome escolhido, o estado, o medidor do microfone e os controles do palco Live', () => {
    const onClose = vi.fn();
    render(
      <LiveVoiceStage
        isOpen={true}
        conversationId="conv-1"
        companionName="Jarvis"
        userNickname="Carlos"
        orbThemeId="ocean"
        phaseOverride="speaking"
        reducedMotion={true}
        onClose={onClose}
      />,
    );

    const stage = screen.getByTestId('live-voice-stage');
    expect(stage.getAttribute('data-voice-state')).toBe('speaking');
    expect(stage.getAttribute('data-reduced-motion')).toBe('true');
    expect(stage.getAttribute('aria-label')).toBe('Conversa por voz com Jarvis');
    expect(screen.getByTestId('live-voice-companion-name').textContent).toBe('Jarvis');
    expect(screen.getByTestId('live-voice-state-label').textContent).toBe('Jarvis está falando');
    expect(screen.getByTestId('live-mic-meter').getAttribute('role')).toBe('meter');
    expect(screen.getByTestId('live-voice-captions').textContent).toContain('Oi, Carlos!');

    fireEvent.click(screen.getByTestId('live-voice-ptt-button'));
    expect(screen.getByTestId('live-voice-ptt-button').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByTestId('live-voice-mute-button'));
    expect(screen.getByTestId('live-voice-mute-button').getAttribute('aria-pressed')).toBe('true');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('rotula cada fase da conversa em português', () => {
    expect(stateLabel('listening')).toBe('Estou ouvindo');
    expect(stateLabel('hearing')).toBe('Ouvindo você…');
    expect(stateLabel('transcribing')).toContain('Entendendo');
    expect(stateLabel('working', 'Orin', 'Rodando pytest')).toBe('Rodando pytest');
    expect(stateLabel('speaking', 'Jarvis')).toBe('Jarvis está falando');
    expect(stateLabel('muted')).toBe('Microfone desligado');
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

  it('abre a configuração de voz e escolhe motor, modelo e a voz de cada modelo (ElevenLabs v3 ≠ v4 pelo OpenRouter)', async () => {
    let whisperState = 'idle';
    const elevenV3 = ['adam', 'george'];
    const elevenV4 = ['sarah', 'bella', 'will'];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
        const url = typeof input === 'string' ? input : input.toString();
        const method = init?.method ?? 'GET';
        const json = (body: unknown, status = 200) =>
          new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
        const install = (engine: string, state: string) => ({ engine, label: engine, installed: state === 'installed', state, step: '', message: '', device: state === 'installed' ? 'cuda' : null, size_hint: '~1 GB', gpu: true });
        if (url.endsWith('/v1/live-voice/capabilities')) {
          return json({
            stt: {
              local_sidecar: { available: whisperState === 'installed', device: 'cuda', model_id: 'faster-whisper-large-v3-turbo', reason: '', install: install('whisper', whisperState) },
              openai: { available: false, model_id: 'gpt-4o-mini-transcribe', reason: '' },
              openrouter: { available: true, model_id: 'openai/whisper-large-v3-turbo', reason: '', models: [{ id: 'openai/whisper-large-v3-turbo', name: 'Whisper' }] },
              groq: { available: false, model_id: 'whisper-large-v3-turbo', reason: '' },
            },
            tts: {
              openai: { available: false, models: [], voices: [], reason: '' },
              openrouter: {
                available: true,
                model_id: 'elevenlabs/eleven-v3',
                reason: '',
                models: [
                  { id: 'elevenlabs/eleven-v3', name: 'ElevenLabs: Eleven v3', vendor: 'elevenlabs', voices: elevenV3 },
                  { id: 'elevenlabs/eleven-v4', name: 'ElevenLabs: Eleven v4', vendor: 'elevenlabs', voices: elevenV4 },
                  { id: 'google/gemini-3.8-flash-tts', name: 'Gemini TTS', vendor: 'google', voices: ['Kore', 'Puck'], supports_instructions: true },
                ],
              },
              kokoro: { available: true, model_id: 'kokoro-82m-onnx', voices: ['pf_dora', 'pm_alex', 'af_bella'], models: [{ id: 'kokoro-82m-onnx', name: 'Kokoro', voices: ['pf_dora', 'pm_alex', 'af_bella'] }], install: install('kokoro', 'installed') },
              chatterbox: { available: false, model_id: 'chatterbox-multilingual', voices: ['default'], voice_clips: [{ id: 'default', name: 'Voz padrão do Chatterbox' }], install: install('chatterbox', 'idle') },
            },
            recommended_stt: 'openrouter',
            recommended_tts: 'openrouter',
            hardware: { has_cuda: true, device_name: 'NVIDIA GeForce RTX 4060', free_vram_gb: 6, total_vram_gb: 8 },
          });
        }
        if (url.endsWith('/v1/live-voice/engines/whisper/install') && method === 'POST') {
          whisperState = 'running';
          return json(install('whisper', 'running'), 202);
        }
        return json({ items: [] });
      }),
    );

    render(<LiveVoiceStage isOpen={true} conversationId="conv-1" companionName="Jarvis" phaseOverride="listening" reducedMotion onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId('live-voice-open-settings-button'));

    const panel = await screen.findByTestId('live-voice-quick-settings');
    expect(panel.textContent).toContain('Configurar voz de Jarvis');
    expect(await screen.findByText('Usa o melhor disponível agora: OpenRouter.')).toBeTruthy();
    expect(screen.getByTestId('live-stt-option-local_sidecar').textContent).toContain('Não instalado');
    expect(screen.getByTestId('live-tts-option-kokoro').textContent).toContain('Pronto · GPU');

    fireEvent.click(screen.getByTestId('live-install-whisper-button'));
    await waitFor(() => expect(screen.getByTestId('live-install-whisper-button').textContent).toBe('Instalando…'));

    // Automático fala pelo OpenRouter: o modelo padrão (Eleven v3) mostra as vozes dele.
    const voicePicker = screen.getByTestId('live-quick-voice-picker');
    const voiceSelect = () => within(voicePicker).getByTestId('live-quick-tts-select') as HTMLSelectElement;
    expect([...voiceSelect().options].map((option) => option.value)).toEqual(elevenV3);
    fireEvent.change(within(voicePicker).getByTestId('live-quick-tts-model-select'), { target: { value: 'elevenlabs/eleven-v4' } });
    await waitFor(() => expect([...voiceSelect().options].map((option) => option.value)).toEqual(elevenV4));
    fireEvent.change(voiceSelect(), { target: { value: 'bella' } });
    expect(loadLiveVoicePrefs().ttsVoices['openrouter:elevenlabs/eleven-v4']).toBe('bella');

    // Cada motor guarda sua própria voz: no Kokoro a voz em português vem primeiro.
    fireEvent.click(within(screen.getByTestId('live-tts-option-kokoro')).getByRole('radio'));
    await waitFor(() => expect(voiceSelect().options[0].textContent).toBe('Dora · português BR · feminina'));
    expect(await screen.findByText('✓ Salvo neste dispositivo')).toBeTruthy();
    expect(loadLiveVoicePrefs().tts).toBe('kokoro');
    expect(loadLiveVoicePrefs().ttsVoices['openrouter:elevenlabs/eleven-v4']).toBe('bella');
  });
});

describe('6. Motor de áudio do modo Live', () => {
  const frame = (amplitude: number) => {
    const out = new Int16Array(LIVE_FRAME_SAMPLES);
    for (let i = 0; i < out.length; i += 1) out[i] = Math.round(Math.sin(i / 3) * amplitude * 32767);
    return out;
  };

  it('abre uma fala com voz contínua e fecha depois do silêncio configurado, guardando o pré-roll', () => {
    const vad = new EnergyVad({ sensitivity: 0.6, endSilenceMs: 320 });
    const events = [];
    for (let i = 0; i < 20; i += 1) events.push(...vad.process(frame(0.0005), 0.0004));
    expect(events).toHaveLength(0);
    for (let i = 0; i < 10; i += 1) events.push(...vad.process(frame(0.2), 0.14));
    expect(events[0]?.type).toBe('speech_start');
    for (let i = 0; i < 12; i += 1) events.push(...vad.process(frame(0.0005), 0.0004));
    const end = events.find((event) => event.type === 'speech_end');
    expect(end).toBeDefined();
    if (end?.type === 'speech_end') expect(end.frames.length).toBeGreaterThan(10);
  });

  it('exige mais energia enquanto o assistente fala (ducking) e o push-to-talk fecha a fala na hora', () => {
    const vad = new EnergyVad({ sensitivity: 0.6 });
    const base = vad.threshold();
    vad.setDucking(true);
    expect(vad.threshold()).toBeCloseTo(base * 3.5);
    expect(DUCKING_MULTIPLIER).toBe(3.5);
    expect(DUCKING_MIN_SPEECH_MS).toBe(420);
    vad.setDucking(false);
    for (let i = 0; i < 5; i += 1) vad.process(frame(0.2), 0.14);
    expect(vad.speaking).toBe(true);
    expect(vad.flush()?.type).toBe('speech_end');
    expect(vad.speaking).toBe(false);
  });

  it('converte o nível do microfone para um medidor perceptivo e reamostra para 16 kHz', () => {
    expect(levelFromRms(0)).toBe(0);
    expect(levelFromRms(0.001)).toBe(0);
    expect(levelFromRms(0.25)).toBeCloseTo(1, 0);
    const resampler = new StreamingResampler(48000);
    const out = [...Array(10)].reduce((total) => total + resampler.process(new Float32Array(480)).length, 0);
    expect(Math.abs(out - 1600)).toBeLessThanOrEqual(1);
  });

  it('escolhe sozinho o melhor jeito de ouvir e de falar entre o que está disponível', () => {
    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      stt: { ...UNAVAILABLE_CAPABILITIES.stt, local_sidecar: { available: true, reason: '', modelId: 'faster-whisper-large-v3-turbo', models: [], voices: [] } },
    };
    expect(resolveStt('auto', caps, true)).toBe('local_sidecar');
    expect(resolveStt('auto', UNAVAILABLE_CAPABILITIES, true)).toBe('browser');
    expect(resolveStt('auto', UNAVAILABLE_CAPABILITIES, false)).toBeNull();
    expect(resolveStt('openai', caps, true)).toBeNull();
    expect(resolveTts('auto', UNAVAILABLE_CAPABILITIES)).toBe('system');
    expect(resolveTts('openai', UNAVAILABLE_CAPABILITIES)).toBe('system');
  });

  it('fala só o texto, sem markdown, links ou marcadores de cena', () => {
    expect(textForSpeech('**Pronto!** Veja [o guia](https://x.dev) [riso leve] e `npm test`.')).toBe('Pronto! Veja o guia e npm test.');
    expect(textForSpeech('```py\nprint(1)\n```\n- Item')).toBe('Item');
  });

  it('guarda modelo e voz por motor e cai para uma voz válida quando o modelo muda', () => {
    const model = (id: string, voices: string[]) => ({ id, name: id, vendor: id.split('/')[0], voices, supportsInstructions: false });
    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        openrouter: { available: true, reason: '', modelId: 'elevenlabs/eleven-v3', voices: [], models: [model('elevenlabs/eleven-v3', ['adam']), model('elevenlabs/eleven-v4', ['sarah', 'bella'])] },
      },
      recommendedTts: 'openrouter' as const,
    };
    const prefs = normalizeLiveVoicePrefs({ ttsModels: { openrouter: 'elevenlabs/eleven-v4' }, ttsVoices: { 'openrouter:elevenlabs/eleven-v4': 'bella', 'openrouter:elevenlabs/eleven-v3': 'bella' } });
    expect(resolveTts('auto', caps)).toBe('openrouter');
    expect(ttsSelection(prefs, caps, 'openrouter')).toMatchObject({ modelId: 'elevenlabs/eleven-v4', voice: 'bella' });
    // 'bella' does not exist in v3: the model's own first voice is used instead.
    expect(ttsSelection({ ...prefs, ttsModels: { openrouter: 'elevenlabs/eleven-v3' } }, caps, 'openrouter')).toMatchObject({ voice: 'adam' });
    // A model that vanished from the catalog falls back to the default model.
    expect(ttsSelection({ ...prefs, ttsModels: { openrouter: 'gone/model' } }, caps, 'openrouter').modelId).toBe('elevenlabs/eleven-v3');
    expect(normalizeLiveVoicePrefs({ openaiVoice: 'nova' }).ttsVoices['openai:gpt-4o-mini-tts']).toBe('nova');
    expect(voiceLabel('kokoro', 'pm_alex')).toBe('Alex · português BR · masculina');
  });
});

describe('7. Correções Anti-Corte, Recuperação de Mudo Permanente, Idioma pt-BR e Integração Live Mode', () => {
  it('envia live_mode: true apenas quando liveMode === true em sendConversationMessage', async () => {
    const requestMock = vi.fn().mockResolvedValue({
      conversation_id: 'conv-1',
      title: 'Conversa',
      turn_id: 'turn-1',
      message_id: 'msg-1',
      state: 'running',
      mode: 'chat',
      permission_mode: 'ask_permissions',
    });
    const fakeClient = {
      request: requestMock,
      createMutationIntent: () => ({ idempotencyKey: 'key-1' }),
    } as unknown as ApiClient;

    await sendConversationMessage(fakeClient, 'conv-1', {
      message: 'Fale comigo ao vivo',
      liveMode: true,
    });
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock.mock.calls[0][0].body).toEqual({
      message: 'Fale comigo ao vivo',
      attachments: [],
      live_mode: true,
    });

    await sendConversationMessage(fakeClient, 'conv-1', {
      message: 'Mensagem comum',
      liveMode: false,
    });
    expect(requestMock.mock.calls[1][0].body).toEqual({
      message: 'Mensagem comum',
      attachments: [],
    });
    expect('live_mode' in requestMock.mock.calls[1][0].body).toBe(false);
  });

  it('spokenSummaryOf limpa markdown e NÃO trunca respostas em 420 caracteres; buildAssistantSpeech fala o restante quando <speak> cobre apenas a introdução', () => {
    const helperModule = ChatPageModule as unknown as {
      spokenSummaryOf?: (content: string) => string;
      buildAssistantSpeech?: (
        events: ReadonlyArray<{ eventId: string; type: string; speakText?: string; turnId?: string; parentAgentId?: string }>,
        messages: ReadonlyArray<{ message_id: string; role: 'user' | 'assistant'; content: string; status: string; turn_id?: string | null }>,
      ) => Array<{ id: string; text: string }>;
    };
    expect(typeof helperModule.spokenSummaryOf).toBe('function');
    expect(typeof helperModule.buildAssistantSpeech).toBe('function');

    const longMarkdown = [
      '# Visão Geral do Orin',
      'Fala sério. Eu sou uma IA assistente rodando aqui na sua máquina. Aqui vai a tradução direta do que faço:',
      '',
      '- **Automação e código**: Eu escrevo, testo, refatoro e corrijo sistemas complexos em Python, TypeScript e Rust.',
      '- **Memória de longo prazo**: Eu lembro das nossas conversas anteriores, das suas preferências de arquitetura e do seu estilo.',
      '- **Execução local segura**: Posso rodar comandos no terminal, inspecionar logs e validar tudo visualmente no navegador.',
      '- **Modo Voz Ao Vivo**: Converso com você em português do Brasil com latência baixa e suporte a barge-in natural.',
      '```ts',
      'const secret = "nao deve ser lido em voz alta";',
      '```',
      'Se quiser começar agora, é só me dizer qual projeto vamos atacar primeiro!',
    ].join('\n');

    const summary = helperModule.spokenSummaryOf!(longMarkdown);
    expect(summary.length).toBeGreaterThan(450);
    expect(summary).toContain('Se quiser começar agora, é só me dizer qual projeto vamos atacar primeiro!');
    expect(summary).not.toContain('nao deve ser lido em voz alta');
    expect(summary).not.toContain('**');
    expect(summary).not.toContain('#');

    // Caso 2b: O LLM só envolveu a introdução em <speak>...:</speak> e deixou os bullets fora da tag!
    const partialSpeakIntro = 'Fala sério. Eu sou uma IA assistente rodando aqui na sua máquina. Aqui vai a tradução direta do que faço:';
    const speechItems = helperModule.buildAssistantSpeech!(
      [
        {
          eventId: 'ev-1',
          type: 'speak.chunk_ready',
          speakText: partialSpeakIntro,
          turnId: 'turn-42',
        },
      ],
      [
        {
          message_id: 'msg-42',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-42',
          content: `<speak>${partialSpeakIntro}</speak>\n\n- **Automação e código**: Eu escrevo e testo.\n- **Memória**: Lembro de tudo.`,
        },
      ],
    );

    expect(speechItems).toHaveLength(2);
    expect(speechItems[0]).toMatchObject({ id: 'speak:ev-1', text: partialSpeakIntro, turnId: 'turn-42', messageId: 'msg-42' });
    expect(speechItems[1].id).toBe('message-rem:msg-42');
    expect(speechItems[1].text).toContain('Automação e código');
    expect(speechItems[1].text).toContain('Memória');
    expect(speechItems[1].text).not.toContain(partialSpeakIntro);
  });

  it('SpeechOutput prioriza voz pt-BR sobre pt-PT, força lang pt-BR e retém activeUtterance contra GC do Chromium', () => {
    const spokenUtterances: SpeechSynthesisUtterance[] = [];
    const cancelMock = vi.fn();
    const speakMock = vi.fn((u: SpeechSynthesisUtterance) => {
      spokenUtterances.push(u);
    });

    const ptPtVoice = {
      voiceURI: 'ms-raquel-pt-pt',
      name: 'Microsoft Raquel - Portuguese (Portugal)',
      lang: 'pt-PT',
      localService: true,
      default: true,
    } as SpeechSynthesisVoice;
    const ptBrVoice = {
      voiceURI: 'ms-francisca-pt-br',
      name: 'Microsoft Francisca - Portuguese (Brazil)',
      lang: 'pt-BR',
      localService: true,
      default: false,
    } as SpeechSynthesisVoice;

    class FakeUtterance {
      text: string;
      voice: SpeechSynthesisVoice | null = null;
      lang = '';
      rate = 1;
      pitch = 1;
      onboundary: ((ev: { charIndex: number }) => void) | null = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
      }
    }

    vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance);
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {
      value: FakeUtterance,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, 'speechSynthesis', {
      value: {
        speaking: false,
        pending: false,
        getVoices: () => [ptPtVoice, ptBrVoice],
        speak: speakMock,
        cancel: cancelMock,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
      configurable: true,
      writable: true,
    });

    const output = new SpeechOutput({}, { voiceUri: null, rate: 1.05, pitch: 1 });
    output.speakSystem('Olá, tudo bem com você?');

    expect(speakMock).toHaveBeenCalledTimes(1);
    // Não deve chamar cancel() quando speechSynthesis está ocioso antes de speak()
    expect(cancelMock).not.toHaveBeenCalled();
    expect(spokenUtterances[0].voice?.voiceURI).toBe('ms-francisca-pt-br');
    expect(spokenUtterances[0].lang).toBe('pt-BR');
    // Deve reter a referência contra GC enquanto fala
    expect((output as unknown as { activeUtterance: unknown }).activeUtterance).toBe(spokenUtterances[0]);

    spokenUtterances[0].onend?.({} as SpeechSynthesisEvent);
    expect((output as unknown as { activeUtterance: unknown }).activeUtterance).toBeNull();
  });

  it('MicCapture.open desativa autoGainControl por padrão (caixas de som) e DEFAULT_LIVE_VOICE_PREFS usa 600ms', async () => {
    expect(DEFAULT_LIVE_VOICE_PREFS.endSilenceMs).toBe(600);

    const getUserMediaMock = vi.fn().mockRejectedValue(new DOMException('Denied', 'NotAllowedError'));
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: getUserMediaMock },
      configurable: true,
      writable: true,
    });
    class FakeAudioContext {}
    Object.defineProperty(window, 'AudioContext', {
      value: FakeAudioContext,
      configurable: true,
      writable: true,
    });

    await expect(MicCapture.open({})).rejects.toThrow();
    expect(getUserMediaMock).toHaveBeenCalledWith({
      audio: expect.objectContaining({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
        channelCount: 1,
      }),
    });

    await expect(MicCapture.open({ headphoneMode: true })).rejects.toThrow();
    expect(getUserMediaMock).toHaveBeenLastCalledWith({
      audio: expect.objectContaining({
        autoGainControl: true,
      }),
    });
  });

  it('useLiveVoice: recupera de falso barge-in (stt.empty / echo / ruído curto) sem mutar permanentemente, envia pt-BR e passa partial_text no vad.eou_wait', async () => {
    let serverEventHandler: ((event: LiveVoiceServerEvent) => void) | null = null;
    const sendSpeakTextMock = vi.fn();
    const sendSpeechEndMock = vi.fn();
    const sendConfigMock = vi.fn();
    const sendBargeInMock = vi.fn();
    const sendAudioResetMock = vi.fn();

    const fakeSocket: ConversationsApi.LiveVoiceSocketHandle = {
      sendBargeIn: sendBargeInMock,
      sendSpeakText: sendSpeakTextMock,
      sendPlaybackProgress: vi.fn(),
      sendSpeechEnd: sendSpeechEndMock,
      sendPcmFrame: vi.fn(),
      sendConfig: sendConfigMock,
      sendAudioReset: sendAudioResetMock,
      isOpen: () => true,
      close: vi.fn(),
    };

    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockImplementation((_convId, onEvent) => {
      serverEventHandler = onEvent;
      return fakeSocket;
    });

    let capturedOnFrame: ((frame: Int16Array, rms: number) => void) | undefined;
    vi.spyOn(MicCapture, 'open').mockImplementation(async (opts) => {
      capturedOnFrame = opts?.onFrame;
      return {
        level: 0,
        deviceLabel: 'Mic Teste',
        close: vi.fn(),
      } as unknown as MicCapture;
    });

    vi.spyOn(SpeechOutput.prototype, 'playPcm').mockImplementation(function (this: SpeechOutput, text: string) {
      (this as unknown as { current: unknown }).current = { kind: 'pcm', text, chunkSeq: 1 };
      (this as unknown as { events: { onStart?: (t: string) => void } }).events.onStart?.(text);
    });
    vi.spyOn(SpeechOutput.prototype, 'stop').mockImplementation(function (this: SpeechOutput) {
      (this as unknown as { current: unknown }).current = null;
      (this as unknown as { events: { onIdle?: () => void } }).events.onIdle?.();
    });

    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      stt: {
        ...UNAVAILABLE_CAPABILITIES.stt,
        local_sidecar: { available: true, reason: '', modelId: 'faster-whisper-large-v3-turbo', models: [], voices: [] },
      },
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        kokoro: { available: true, reason: '', modelId: 'kokoro-82m-onnx', voices: ['pf_dora'], models: [] },
      },
      recommendedStt: 'local_sidecar' as const,
      recommendedTts: 'kokoro' as const,
    };

    const fakeClient = {
      socketCsrfToken: () => 'csrf-1',
    } as unknown as ApiClient;

    const onUtterance = vi.fn();
    const { rerender } = renderHook(
      (props: { speech: Array<{ id: string; text: string }>; busy: boolean }) =>
        useLiveVoice({
          active: true,
          conversationId: 'conv-live-1',
          client: fakeClient,
          prefs: DEFAULT_LIVE_VOICE_PREFS,
          capabilities: caps,
          assistantSpeech: props.speech,
          assistantBusy: props.busy,
          onUtterance,
          voiceInstructions: 'Fale SEMPRE em português do Brasil (pt-BR) nativo.',
        }),
      { initialProps: { speech: [] as Array<{ id: string; text: string }>, busy: false } },
    );

    await waitFor(() => expect(capturedOnFrame).toBeDefined());

    // 1. Verifica que sendConfig enviou language: 'pt-BR'
    expect(sendConfigMock).toHaveBeenCalledWith(
      expect.objectContaining({
        language: 'pt-BR',
      } satisfies Partial<LiveVoiceSocketConfig>),
    );

    // 2. Assistente começa a falar o primeiro chunk
    rerender({ speech: [{ id: 'speak:1', text: 'Primeira frase do assistente.' }], busy: true });
    expect(sendSpeakTextMock).toHaveBeenCalledWith(
      'Primeira frase do assistente.',
      'Fale SEMPRE em português do Brasil (pt-BR) nativo.',
    );

    act(() => {
      serverEventHandler?.({
        type: 'tts.chunk',
        epoch: 1,
        chunk_seq: 1,
        clean_text: 'Primeira frase do assistente.',
        pcm_base64: 'AAAA',
        sample_rate: 24000,
        duration_ms: 500,
        word_timings: [],
      });
    });

    // 3. Simula falso alarme de barge-in (pico de ruído que dispara speech_start mas retorna stt.empty)
    const loudFrame = new Int16Array(LIVE_FRAME_SAMPLES).fill(12000);
    act(() => {
      for (let i = 0; i < 20; i += 1) {
        capturedOnFrame?.(loudFrame, 0.45);
      }
    });

    // O servidor responde stt.empty (falso alarme / suspiro / eco descartado)
    act(() => {
      serverEventHandler?.({ type: 'stt.empty', audio_epoch: 1 });
    });

    // 4. Novo item de fala do assistente chega: NÃO pode estar mutado por suppressRef!
    rerender({
      speech: [
        { id: 'speak:1', text: 'Primeira frase do assistente.' },
        { id: 'speak:2', text: 'Segunda frase que não pode ser cortada!' },
      ],
      busy: true,
    });

    expect(sendSpeakTextMock).toHaveBeenCalledTimes(2);
    expect(sendSpeakTextMock).toHaveBeenLastCalledWith(
      'Segunda frase que não pode ser cortada!',
      'Fale SEMPRE em português do Brasil (pt-BR) nativo.',
    );

    // 5. Verifica que vad.eou_wait repassa event.partial_text para sendSpeechEnd
    vi.useFakeTimers();
    try {
      act(() => {
        serverEventHandler?.({
          type: 'vad.eou_wait',
          is_end_of_utterance: false,
          required_silence_ms: 600,
          silence_ms: 300,
          partial_text: 'Eu queria saber se',
          detected_hold_marker: 'se',
        });
      });
      act(() => {
        vi.advanceTimersByTime(400);
      });
      expect(sendSpeechEndMock).toHaveBeenCalledWith(
        'Eu queria saber se',
        600,
        { sample_rate: 16000 },
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('adversarial: buildAssistantSpeech alinha por palavras quando pontuação diverge, não duplica fala completa e spokenSummaryOf respeita teto de 2400 chars', () => {
    const { spokenSummaryOf, buildAssistantSpeech } = ChatPageModule;

    // Pontuação ligeiramente diferente entre o evento speak.chunk_ready (limpo pelo backend) e o markdown bruto
    const itemsWithPunctDiff = buildAssistantSpeech(
      [
        {
          eventId: 'ev-punct',
          type: 'speak.chunk_ready',
          speakText: 'Olha so, aqui estao os tres pilares principais:',
          turnId: 'turn-punct',
        },
      ],
      [
        {
          message_id: 'msg-punct',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-punct',
          content: '**Olha só** — aqui estão os três pilares principais:\n\n1. Segurança total\n2. Baixa latência\n3. Código limpo',
        },
      ],
    );
    expect(itemsWithPunctDiff).toHaveLength(2);
    expect(itemsWithPunctDiff[1].id).toBe('message-rem:msg-punct');
    expect(itemsWithPunctDiff[1].text).toContain('1. Segurança total');
    expect(itemsWithPunctDiff[1].text).not.toContain('pilares principais');

    // Quando <speak> já cobriu a resposta inteira, não deve adicionar message-rem duplicado
    const completeText = 'Pronto, já atualizei a configuração do servidor e rodei todos os testes com sucesso.';
    const itemsComplete = buildAssistantSpeech(
      [
        {
          eventId: 'ev-full',
          type: 'speak.chunk_ready',
          speakText: completeText,
          turnId: 'turn-full',
        },
      ],
      [
        {
          message_id: 'msg-full',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-full',
          content: `<speak>${completeText}</speak>`,
        },
      ],
    );
    expect(itemsComplete).toHaveLength(1);
    expect(itemsComplete[0].id).toBe('speak:ev-full');

    // Texto gigante (> 3000 chars) deve ser cortado de forma limpa em limite de sentença <= 2400 chars
    const repeatedSentence = 'Esta é uma frase explicativa detalhada sobre a arquitetura do sistema Orin. ';
    const hugeText = repeatedSentence.repeat(50);
    const bounded = spokenSummaryOf(hugeText);
    expect(bounded.length).toBeGreaterThan(1800);
    expect(bounded.length).toBeLessThanOrEqual(2400);
    expect(bounded.endsWith('.')).toBe(true);
  });

  it('adversarial: useLiveVoice mantém ducking ativo entre chunks TTS em trânsito e recupera de eco lexical no cliente', async () => {
    let serverEventHandler: ((event: LiveVoiceServerEvent) => void) | null = null;
    const sendSpeakTextMock = vi.fn();
    const setDuckingSpy = vi.spyOn(EnergyVad.prototype, 'setDucking');

    const fakeSocket: ConversationsApi.LiveVoiceSocketHandle = {
      sendBargeIn: vi.fn(),
      sendSpeakText: sendSpeakTextMock,
      sendPlaybackProgress: vi.fn(),
      sendSpeechEnd: vi.fn(),
      sendPcmFrame: vi.fn(),
      sendConfig: vi.fn(),
      sendAudioReset: vi.fn(),
      isOpen: () => true,
      close: vi.fn(),
    };

    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockImplementation((_convId, onEvent) => {
      serverEventHandler = onEvent;
      return fakeSocket;
    });

    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture);

    let triggerIdle: (() => void) | null = null;
    vi.spyOn(SpeechOutput.prototype, 'playPcm').mockImplementation(function (this: SpeechOutput, text: string) {
      (this as unknown as { current: unknown }).current = { kind: 'pcm', text, chunkSeq: 1 };
      const evs = (this as unknown as { events: { onStart?: (t: string) => void; onIdle?: () => void } }).events;
      evs.onStart?.(text);
      triggerIdle = () => {
        (this as unknown as { current: unknown }).current = null;
        evs.onIdle?.();
      };
    });

    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      stt: {
        ...UNAVAILABLE_CAPABILITIES.stt,
        local_sidecar: { available: true, reason: '', modelId: 'faster-whisper-large-v3-turbo', models: [], voices: [] },
      },
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        kokoro: { available: true, reason: '', modelId: 'kokoro-82m-onnx', voices: ['pf_dora'], models: [] },
      },
      recommendedStt: 'local_sidecar' as const,
      recommendedTts: 'kokoro' as const,
    };

    const fakeClient = { socketCsrfToken: () => 'csrf-1' } as unknown as ApiClient;
    const onUtterance = vi.fn();

    const { rerender } = renderHook(
      (props: { speech: Array<{ id: string; text: string }> }) =>
        useLiveVoice({
          active: true,
          conversationId: 'conv-adv-1',
          client: fakeClient,
          prefs: DEFAULT_LIVE_VOICE_PREFS,
          capabilities: caps,
          assistantSpeech: props.speech,
          assistantBusy: true,
          onUtterance,
        }),
      { initialProps: { speech: [] as Array<{ id: string; text: string }> } },
    );

    // Envia 2 chunks para síntese (pendingTtsCount = 2)
    rerender({
      speech: [
        { id: 's1', text: 'Claro, já estou verificando os arquivos do projeto agora mesmo.' },
        { id: 's2', text: 'Assim que terminar te aviso o resultado.' },
      ],
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(2);

    // Chunk 1 chega e toca
    act(() => {
      serverEventHandler?.({
        type: 'tts.chunk',
        epoch: 1,
        chunk_seq: 1,
        clean_text: 'Claro, já estou verificando os arquivos do projeto agora mesmo.',
        pcm_base64: 'AAAA',
        sample_rate: 24000,
        duration_ms: 400,
        word_timings: [],
      });
    });

    // Chunk 1 termina de tocar no cliente ANTES do Chunk 2 chegar da rede (pendingTtsCount === 1 > 0)
    vi.useFakeTimers();
    try {
      setDuckingSpy.mockClear();
      act(() => {
        triggerIdle?.();
        vi.advanceTimersByTime(300);
      });
      // Ducking NÃO pode ter sido desligado (setDucking(false)) enquanto o Chunk 2 ainda está em trânsito!
      expect(setDuckingSpy).not.toHaveBeenCalledWith(false);

      // Agora chega transcrição que é eco óbvio do Chunk 1: deve ser descartada sem chamar onUtterance
      act(() => {
        serverEventHandler?.({
          type: 'stt.transcript',
          is_end_of_utterance: true,
          text: 'claro ja estou verificando os arquivos do projeto agora mesmo',
          audio_epoch: 1,
        });
      });
      expect(onUtterance).not.toHaveBeenCalled();

      // Chunk 2 chega e termina: agora sim (pendingTtsCount === 0) o ducking é liberado após 250ms
      act(() => {
        serverEventHandler?.({
          type: 'tts.chunk',
          epoch: 1,
          chunk_seq: 2,
          clean_text: 'Assim que terminar te aviso o resultado.',
          pcm_base64: 'AAAA',
          sample_rate: 24000,
          duration_ms: 400,
          word_timings: [],
        });
      });
      act(() => {
        triggerIdle?.();
        vi.advanceTimersByTime(300);
      });
      expect(setDuckingSpy).toHaveBeenCalledWith(false);
    } finally {
      vi.useRealTimers();
    }
  });
});


describe('8. Hardening Cross-Stack Audit 3B (Deduplicação, Histórico/Resync, Barge-In em Turno Ativo e Sincronização de Ducking)', () => {
  it('8.1 elimina fala duplicada entre speak.chunk_ready (com PTBRTextNormalizer) e message-rem, extrai cleanText e bloqueia speak tardio após message-rem', async () => {
    const parsedEvent = ConversationsApi.parseConversationActivityEvent(
      {
        event_id: 'ev-norm-1',
        event_type: 'speak.chunk_ready',
        turn_id: 'turn-norm-1',
        payload: {
          clean_text: 'Reunião marcada às 14h30 para revisar a API por R$ 10 com toda a equipe:',
          tts_text: 'Reunião marcada às 14 horas e 30 minutos para revisar a A P I por 10 reais com toda a equipe:',
          message_id: 'msg-norm-1',
        },
      },
      'cursor-1',
    );
    expect(parsedEvent.speakText).toBe(
      'Reunião marcada às 14 horas e 30 minutos para revisar a A P I por 10 reais com toda a equipe:',
    );
    expect((parsedEvent as { cleanText?: string }).cleanText).toBe(
      'Reunião marcada às 14h30 para revisar a API por R$ 10 com toda a equipe:',
    );

    const { buildAssistantSpeech } = ChatPageModule;

    // Quando o texto falado já alcançou o fim da mensagem (mesmo com expansão TTS de números/siglas e terminando com ':'),
    // NÃO deve emitir message-rem duplicado!
    const fullNormalizedItems = buildAssistantSpeech(
      [
        {
          eventId: 'ev-norm-full',
          type: 'speak.chunk_ready',
          speakText: 'Reunião marcada às 14 horas e 30 minutos para revisar a A P I por 10 reais com toda a equipe:',
          turnId: 'turn-norm-full',
        },
      ],
      [
        {
          message_id: 'msg-norm-full',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-norm-full',
          content: '<speak>Reunião marcada às 14h30 para revisar a API por R$ 10 com toda a equipe:</speak>',
        },
      ],
    );
    expect(fullNormalizedItems).toHaveLength(1);
    expect(fullNormalizedItems[0]).toMatchObject({
      id: 'speak:ev-norm-full',
      turnId: 'turn-norm-full',
      messageId: 'msg-norm-full',
    });

    // Quando <speak> cobriu apenas a introdução com números/siglas normalizados e há itens reais depois,
    // deve extrair APENAS o restante e anexar turnId/messageId
    const partialNormalizedItems = buildAssistantSpeech(
      [parsedEvent],
      [
        {
          message_id: 'msg-norm-1',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-norm-1',
          content:
            '<speak>Reunião marcada às 14h30 para revisar a API por R$ 10 com toda a equipe:</speak>\n\n- **Pauta 1**: Revisar contrato REST\n- **Pauta 2**: Validar autenticação JWT',
        },
      ],
    );
    expect(partialNormalizedItems).toHaveLength(2);
    expect(partialNormalizedItems[1]).toMatchObject({
      id: 'message-rem:msg-norm-1',
      turnId: 'turn-norm-1',
      messageId: 'msg-norm-1',
    });
    expect(partialNormalizedItems[1].text).toContain('Pauta 1');
    expect(partialNormalizedItems[1].text).not.toContain('Reunião marcada');

    // No useLiveVoice: se message-rem já foi falado para um turnId, um speak: tardio do mesmo turnId é ignorado
    const sendSpeakTextMock = vi.fn();
    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockReturnValue({
      sendBargeIn: vi.fn(),
      sendSpeakText: sendSpeakTextMock,
      sendPlaybackProgress: vi.fn(),
      sendSpeechEnd: vi.fn(),
      sendPcmFrame: vi.fn(),
      sendConfig: vi.fn(),
      sendAudioReset: vi.fn(),
      isOpen: () => true,
      close: vi.fn(),
    });
    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture);

    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        kokoro: { available: true, reason: '', modelId: 'kokoro-82m-onnx', voices: ['pf_dora'], models: [] },
      },
      recommendedTts: 'kokoro' as const,
    };
    const fakeClient = { socketCsrfToken: () => 'csrf-1' } as unknown as ApiClient;

    const { rerender } = renderHook(
      (props: { speech: Array<{ id: string; text: string; turnId?: string; messageId?: string }> }) =>
        useLiveVoice({
          active: true,
          conversationId: 'conv-dedup-1',
          client: fakeClient,
          prefs: DEFAULT_LIVE_VOICE_PREFS,
          capabilities: caps,
          assistantSpeech: props.speech,
          assistantBusy: true,
          onUtterance: vi.fn(),
        }),
      { initialProps: { speech: [] as Array<{ id: string; text: string; turnId?: string; messageId?: string }> } },
    );

    rerender({
      speech: [
        { id: 'speak:ev-1', text: 'Introdução:', turnId: 'turn-dup', messageId: 'msg-dup' },
        { id: 'message-rem:msg-dup', text: 'Restante importante da resposta.', turnId: 'turn-dup', messageId: 'msg-dup' },
      ],
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(2);

    // Chega um speak:ev-late atrasado do mesmo turn-dup após message-rem já ter sido falado: deve ser ignorado!
    rerender({
      speech: [
        { id: 'speak:ev-1', text: 'Introdução:', turnId: 'turn-dup', messageId: 'msg-dup' },
        { id: 'speak:ev-late', text: 'Restante importante da resposta.', turnId: 'turn-dup', messageId: 'msg-dup' },
      ],
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(2);
  });

  it('8.2 impede leitura de mensagens históricas em snapshot tardio, SSE resync ou despejo de eventos', async () => {
    const { buildAssistantSpeech } = ChatPageModule;

    // Quando há múltiplas mensagens de assistente concluídas e events está vazio (após resync/eviction),
    // buildAssistantSpeech SÓ pode incluir a última mensagem concluída do assistente!
    const afterResyncItems = buildAssistantSpeech(
      [],
      [
        {
          message_id: 'msg-old-1',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-old-1',
          content: 'Primeira resposta histórica antiga que jamais deve ser relida.',
        },
        {
          message_id: 'msg-user-2',
          role: 'user',
          status: 'completed',
          turn_id: 'turn-2',
          content: 'Pergunta seguinte',
        },
        {
          message_id: 'msg-latest-2',
          role: 'assistant',
          status: 'completed',
          turn_id: 'turn-2',
          content: 'Resposta mais recente da conversa.',
        },
      ],
    );
    expect(afterResyncItems).toHaveLength(1);
    expect(afterResyncItems[0]).toMatchObject({
      id: 'message:msg-latest-2',
      turnId: 'turn-2',
      messageId: 'msg-latest-2',
    });

    const sendSpeakTextMock = vi.fn();
    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockReturnValue({
      sendBargeIn: vi.fn(),
      sendSpeakText: sendSpeakTextMock,
      sendPlaybackProgress: vi.fn(),
      sendSpeechEnd: vi.fn(),
      sendPcmFrame: vi.fn(),
      sendConfig: vi.fn(),
      sendAudioReset: vi.fn(),
      isOpen: () => true,
      close: vi.fn(),
    });
    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture);

    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        kokoro: { available: true, reason: '', modelId: 'kokoro-82m-onnx', voices: ['pf_dora'], models: [] },
      },
      recommendedTts: 'kokoro' as const,
    };
    const fakeClient = { socketCsrfToken: () => 'csrf-1' } as unknown as ApiClient;

    // Caso A: Palco abre enquanto loading === true (speech = [], busy = false).
    // 50ms depois loadSnapshot termina e popula histórico com busy = false. NÃO deve falar o histórico!
    const { rerender } = renderHook(
      (props: { speech: Array<{ id: string; text: string; turnId?: string; messageId?: string }>; busy: boolean }) =>
        useLiveVoice({
          active: true,
          conversationId: 'conv-resync-1',
          client: fakeClient,
          prefs: DEFAULT_LIVE_VOICE_PREFS,
          capabilities: caps,
          assistantSpeech: props.speech,
          assistantBusy: props.busy,
          onUtterance: vi.fn(),
        }),
      { initialProps: { speech: [] as Array<{ id: string; text: string; turnId?: string; messageId?: string }>, busy: false } },
    );

    rerender({
      speech: [
        { id: 'message:msg-hist-1', text: 'Histórico carregado após 50ms.', turnId: 'turn-hist-1', messageId: 'msg-hist-1' },
      ],
      busy: false,
    });
    expect(sendSpeakTextMock).not.toHaveBeenCalled();

    // Caso B: Um turno novo (turn-live-2) roda e fala via speak:ev-live-2; depois um SSE resync limpa events
    // trocando o item por message:msg-live-2 (mesmo turnId: 'turn-live-2'). NÃO deve falar novamente!
    rerender({
      speech: [
        { id: 'speak:ev-live-2', text: 'Fala ao vivo do turno 2.', turnId: 'turn-live-2', messageId: 'msg-live-2' },
      ],
      busy: true,
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(1);

    rerender({
      speech: [
        { id: 'message:msg-live-2', text: 'Fala ao vivo do turno 2.', turnId: 'turn-live-2', messageId: 'msg-live-2' },
      ],
      busy: false,
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(1);
  });

  it('8.3 botão manual Interromper silencia o restante do turno atual mesmo em silêncio, e barge-in no ChatPage durante turno running cancela e envia imediatamente', async () => {
    let serverEventHandler: ((event: LiveVoiceServerEvent) => void) | null = null;
    const sendSpeakTextMock = vi.fn();

    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockImplementation((_convId, onEvent) => {
      serverEventHandler = onEvent;
      return {
        sendBargeIn: vi.fn(),
        sendSpeakText: sendSpeakTextMock,
        sendPlaybackProgress: vi.fn(),
        sendSpeechEnd: vi.fn(),
        sendPcmFrame: vi.fn(),
        sendConfig: vi.fn(),
        sendAudioReset: vi.fn(),
        isOpen: () => true,
        close: vi.fn(),
      };
    });
    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture);

    vi.spyOn(SpeechOutput.prototype, 'playPcm').mockImplementation(function (this: SpeechOutput, text: string) {
      (this as unknown as { current: unknown }).current = { kind: 'pcm', text, chunkSeq: 1 };
      (this as unknown as { events: { onStart?: (t: string) => void } }).events.onStart?.(text);
    });
    vi.spyOn(SpeechOutput.prototype, 'stop').mockImplementation(function (this: SpeechOutput) {
      (this as unknown as { current: unknown }).current = null;
      (this as unknown as { events: { onIdle?: () => void } }).events.onIdle?.();
    });

    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        kokoro: { available: true, reason: '', modelId: 'kokoro-82m-onnx', voices: ['pf_dora'], models: [] },
      },
      recommendedTts: 'kokoro' as const,
    };
    const fakeClient = { socketCsrfToken: () => 'csrf-1' } as unknown as ApiClient;

    const { result, rerender } = renderHook(
      (props: { speech: Array<{ id: string; text: string; turnId?: string }>; busy: boolean }) =>
        useLiveVoice({
          active: true,
          conversationId: 'conv-manual-int',
          client: fakeClient,
          prefs: DEFAULT_LIVE_VOICE_PREFS,
          capabilities: caps,
          assistantSpeech: props.speech,
          assistantBusy: props.busy,
          onUtterance: vi.fn(),
        }),
      { initialProps: { speech: [] as Array<{ id: string; text: string; turnId?: string }>, busy: true } },
    );

    // Chunk 1 começa a falar
    rerender({ speech: [{ id: 'speak:1', text: 'Chunk 1 falando agora.', turnId: 'turn-1' }], busy: true });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(1);
    act(() => {
      serverEventHandler?.({
        type: 'tts.chunk',
        epoch: 1,
        chunk_seq: 1,
        clean_text: 'Chunk 1 falando agora.',
        pcm_base64: 'AAAA',
        sample_rate: 24000,
        duration_ms: 400,
        word_timings: [],
      });
    });

    // Usuário clica no botão "Interromper" (interrupt manual) e fica em silêncio (!userSpeaking && !transcribing)
    act(() => {
      result.current.interrupt();
    });

    // Mesmo se chegar stt.empty de ruído ambiente, o interrupt manual NÃO pode ser desfeito no mesmo turno
    act(() => {
      serverEventHandler?.({ type: 'stt.empty', audio_epoch: 1 });
    });

    // Chunk 2 do mesmo turno chega enquanto o usuário está em silêncio: DEVE ser silenciado!
    rerender({
      speech: [
        { id: 'speak:1', text: 'Chunk 1 falando agora.', turnId: 'turn-1' },
        { id: 'speak:2', text: 'Chunk 2 do mesmo turno que deve ficar mudo.', turnId: 'turn-1' },
      ],
      busy: true,
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(1);

    // Quando um NOVO turno começa (busy: false -> true), o novo turno volta a falar normalmente!
    rerender({
      speech: [
        { id: 'speak:1', text: 'Chunk 1 falando agora.', turnId: 'turn-1' },
        { id: 'speak:2', text: 'Chunk 2 do mesmo turno que deve ficar mudo.', turnId: 'turn-1' },
      ],
      busy: false,
    });
    rerender({
      speech: [
        { id: 'speak:1', text: 'Chunk 1 falando agora.', turnId: 'turn-1' },
        { id: 'speak:2', text: 'Chunk 2 do mesmo turno que deve ficar mudo.', turnId: 'turn-1' },
        { id: 'speak:3', text: 'Fala do novo turno 2!', turnId: 'turn-2' },
      ],
      busy: true,
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(2);
    expect(sendSpeakTextMock).toHaveBeenLastCalledWith('Fala do novo turno 2!', undefined);
  });

  it('8.4 ChatPage: barge-in falado enquanto running === true interrompe o turno ativo (cancelConversation) e envia o novo prompt com live_mode: true', async () => {
    const cancelSpy = vi.spyOn(ConversationsApi, 'cancelConversation').mockResolvedValue({ cancelling: ['turn-running-1'] });
    const sendSpy = vi.spyOn(ConversationsApi, 'sendConversationMessage').mockResolvedValue({
      conversation_id: 'conv-barge-1',
      title: 'Conversa',
      turn_id: 'turn-2',
      message_id: 'msg-2',
      state: 'running',
      mode: 'friend',
      permission_mode: 'ask_permissions',
    });
    vi.spyOn(ConversationsApi, 'getConversation').mockResolvedValue({
      conversation_id: 'conv-barge-1',
      title: 'Orin',
      state: 'running',
      mode: 'friend',
      permission_mode: 'ask_permissions',
      messages: [
        {
          message_id: 'msg-1',
          role: 'assistant',
          content: 'Estou explicando algo longo...',
          status: 'streaming',
          retryable: false,
          attachments: [],
          command: null,
          turn_id: 'turn-running-1',
        },
      ],
      turns: [
        {
          turn_id: 'turn-running-1',
          state: 'running',
          created_at: '2026-10-28T00:00:00Z',
          started_at: '2026-10-28T00:00:01Z',
          finished_at: null,
          scheduled_by_schedule_id: null,
          code_mode: null,
        },
      ],
      provider: 'openai',
      model_id: 'gpt-4o',
      activities: [],
      activity_cursor: '1',
      workspace: { kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null },
      context_usage: null,
    });
    vi.spyOn(ConversationsApi, 'listConversations').mockResolvedValue({ items: [] });
    vi.spyOn(ConversationsApi, 'streamConversationEvents').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(ConversationsApi, 'getFriendPersona').mockResolvedValue(SAMPLE_PERSONA);

    let capturedTranscriptCallback: ((event: LiveVoiceServerEvent) => void) | null = null;
    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockImplementation((_convId, onEvent) => {
      capturedTranscriptCallback = onEvent;
      return {
        sendBargeIn: vi.fn(),
        sendSpeakText: vi.fn(),
        sendPlaybackProgress: vi.fn(),
        sendSpeechEnd: vi.fn(),
        sendPcmFrame: vi.fn(),
        sendConfig: vi.fn(),
        sendAudioReset: vi.fn(),
        isOpen: () => true,
        close: vi.fn(),
      };
    });
    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture);

    const { Route, Routes } = await import('react-router-dom');
    render(
      <MemoryRouter initialEntries={['/chats/conv-barge-1']}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPageModule.ChatPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const liveToggle = await screen.findByTestId('composer-live-voice-toggle');
    fireEvent.click(liveToggle);
    await waitFor(() => expect(capturedTranscriptCallback).not.toBeNull());

    // Enquanto o turno 'turn-running-1' ainda está running, o usuário fala um barge-in:
    await act(async () => {
      capturedTranscriptCallback?.({
        type: 'stt.transcript',
        is_end_of_utterance: true,
        text: 'Pare tudo e mude para o plano B agora',
        audio_epoch: 1,
      });
    });

    await waitFor(() => {
      expect(cancelSpy).toHaveBeenCalledWith(expect.anything(), 'conv-barge-1');
      expect(sendSpy).toHaveBeenCalledTimes(1);
      expect(sendSpy.mock.calls[0][1]).toBe('conv-barge-1');
      expect(sendSpy.mock.calls[0][2]).toBe('Pare tudo e mude para o plano B agora');
      expect(sendSpy.mock.calls[0][8]).toBe(true);
    });
  });

  it('8.5 sincroniza pendingTtsCountRef e setDucking em texto vazio, falha de sendSpeakText, tts.error em fila e queda de WebSocket', async () => {
    let serverEventHandler: ((event: LiveVoiceServerEvent) => void) | null = null;
    let statusHandler: ((status: ConversationsApi.LiveVoiceSocketStatus) => void) | undefined;
    const sendSpeakTextMock = vi.fn().mockReturnValue(true);
    const setDuckingSpy = vi.spyOn(EnergyVad.prototype, 'setDucking');
    const speakSystemSpy = vi.spyOn(SpeechOutput.prototype, 'speakSystem').mockImplementation(() => {});

    vi.spyOn(ConversationsApi, 'connectLiveVoiceSession').mockImplementation((_convId, onEvent, opts) => {
      serverEventHandler = onEvent;
      statusHandler = opts?.onStatus;
      return {
        sendBargeIn: vi.fn(),
        sendSpeakText: sendSpeakTextMock,
        sendPlaybackProgress: vi.fn(),
        sendSpeechEnd: vi.fn(),
        sendPcmFrame: vi.fn(),
        sendConfig: vi.fn(),
        sendAudioReset: vi.fn(),
        isOpen: () => true,
        close: vi.fn(),
      };
    });
    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture);

    let finishChunkPlayback: (() => void) | null = null;
    vi.spyOn(SpeechOutput.prototype, 'playPcm').mockImplementation(function (this: SpeechOutput, text: string) {
      (this as unknown as { current: unknown }).current = { kind: 'pcm', text, chunkSeq: 1 };
      const evs = (this as unknown as { events: { onStart?: (t: string) => void; onIdle?: () => void } }).events;
      evs.onStart?.(text);
      finishChunkPlayback = () => {
        (this as unknown as { current: unknown }).current = null;
      };
    });

    const caps = {
      ...UNAVAILABLE_CAPABILITIES,
      tts: {
        ...UNAVAILABLE_CAPABILITIES.tts,
        kokoro: { available: true, reason: '', modelId: 'kokoro-82m-onnx', voices: ['pf_dora'], models: [] },
      },
      recommendedTts: 'kokoro' as const,
    };
    const fakeClient = { socketCsrfToken: () => 'csrf-1' } as unknown as ApiClient;

    const { rerender } = renderHook(
      (props: { speech: Array<{ id: string; text: string }> }) =>
        useLiveVoice({
          active: true,
          conversationId: 'conv-sync-1',
          client: fakeClient,
          prefs: DEFAULT_LIVE_VOICE_PREFS,
          capabilities: caps,
          assistantSpeech: props.speech,
          assistantBusy: true,
          onUtterance: vi.fn(),
        }),
      { initialProps: { speech: [] as Array<{ id: string; text: string }> } },
    );

    // 1. Texto que fica vazio após textForSpeech (ex.: apenas bloco de código) NÃO deve chamar sendSpeakText nem ligar ducking
    setDuckingSpy.mockClear();
    rerender({ speech: [{ id: 'speak:empty', text: '```ts\nconst x = 1;\n```' }] });
    expect(sendSpeakTextMock).not.toHaveBeenCalled();
    expect(setDuckingSpy).not.toHaveBeenCalledWith(true);

    // 2. Se sendSpeakText retornar false, NÃO deve incrementar pendingTtsCount nem prender ducking
    sendSpeakTextMock.mockReturnValueOnce(false);
    rerender({
      speech: [
        { id: 'speak:empty', text: '```ts\nconst x = 1;\n```' },
        { id: 'speak:fail-send', text: 'Tentativa com socket fechado.' },
      ],
    });
    expect(sendSpeakTextMock).toHaveBeenCalledTimes(1);
    expect(setDuckingSpy).not.toHaveBeenCalledWith(true);

    // 3. Chunk 1 toca e Chunk 2 dá tts.error enquanto Chunk 1 ainda está tocando (speaking === true):
    // speakSystem DEVE ser chamado para enfileirar o Chunk 2!
    sendSpeakTextMock.mockReturnValue(true);
    rerender({
      speech: [
        { id: 'speak:empty', text: '```ts\nconst x = 1;\n```' },
        { id: 'speak:fail-send', text: 'Tentativa com socket fechado.' },
        { id: 'speak:c1', text: 'Primeiro chunk tocando.' },
        { id: 'speak:c2', text: 'Segundo chunk que falhou no servidor.' },
      ],
    });
    act(() => {
      serverEventHandler?.({
        type: 'tts.chunk',
        epoch: 1,
        chunk_seq: 1,
        clean_text: 'Primeiro chunk tocando.',
        pcm_base64: 'AAAA',
        sample_rate: 24000,
        duration_ms: 400,
        word_timings: [],
      });
    });
    act(() => {
      serverEventHandler?.({
        type: 'tts.error',
        code: 'tts_failed',
        message: 'Falha no sidecar',
        text: 'Segundo chunk que falhou no servidor.',
      });
    });
    expect(speakSystemSpy).toHaveBeenCalledWith('Segundo chunk que falhou no servidor.');

    // 4. Quando o áudio termina e o WebSocket cai ('closed') com um chunk pendente em trânsito:
    // deve zerar pendingTtsCount e liberar setDucking(false)!
    rerender({
      speech: [
        { id: 'speak:empty', text: '```ts\nconst x = 1;\n```' },
        { id: 'speak:fail-send', text: 'Tentativa com socket fechado.' },
        { id: 'speak:c1', text: 'Primeiro chunk tocando.' },
        { id: 'speak:c2', text: 'Segundo chunk que falhou no servidor.' },
        { id: 'speak:c3', text: 'Terceiro chunk em trânsito antes da queda.' },
      ],
    });
    act(() => {
      finishChunkPlayback?.();
    });
    setDuckingSpy.mockClear();
    act(() => {
      statusHandler?.('closed');
    });
    expect(setDuckingSpy).toHaveBeenCalledWith(false);
  });
});
