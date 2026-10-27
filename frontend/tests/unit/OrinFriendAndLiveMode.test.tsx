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
import { EnergyVad, LIVE_FRAME_SAMPLES, StreamingResampler, levelFromRms } from '../../src/features/conversations/live/liveAudio';
import { textForSpeech } from '../../src/features/conversations/live/liveSpeech';
import { loadLiveVoicePrefs, normalizeLiveVoicePrefs, resolveStt, resolveTts, ttsSelection, voiceLabel } from '../../src/features/conversations/live/liveVoicePrefs';
import { UNAVAILABLE_CAPABILITIES } from '../../src/api/liveVoice';
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
    expect(vad.threshold()).toBeCloseTo(base * 2.8);
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
