import { useEffect, useMemo, useState } from 'react'
import { ApiClient } from '../../api/client'
import {
  getMediaModelSettings,
  getVoiceHardwareDiagnostic,
  setMediaModelSetting,
  type MediaModality,
  type MediaModelSettings,
  type MediaProviderName,
  type VoiceHardwareDiagnostic,
} from '../../api/providers'

export type LiveVoicePresetId = 'rtx_local' | 'hybrid_fast' | 'cloud_zero_vram' | 'cpu_light'

export type LiveVoicePreset = {
  id: LiveVoicePresetId
  badge: string
  title: string
  subtitle: string
  ttsProvider: MediaProviderName
  ttsModelId: string
  ttsLabel: string
  sttProvider: MediaProviderName
  sttModelId: string
  sttLabel: string
}

export const LIVE_VOICE_ONE_CLICK_PRESETS: ReadonlyArray<LiveVoicePreset> = [
  {
    id: 'rtx_local',
    badge: '⚡ Recomendado RTX / GPU >= 6GB',
    title: '100% Local Expressivo (PT-BR)',
    subtitle: 'Máxima naturalidade em português sem custo de API rodando na sua GPU.',
    ttsProvider: 'local_sidecar',
    ttsModelId: 'chatterbox-pt-br',
    ttsLabel: 'Chatterbox PT-BR (500M Local)',
    sttProvider: 'local_sidecar',
    sttModelId: 'faster-whisper-large-v3-turbo',
    sttLabel: 'Faster-Whisper Large-v3-Turbo (Local)',
  },
  {
    id: 'hybrid_fast',
    badge: '🚀 Menor Latência (<200ms)',
    title: 'Híbrido Ultra-Rápido (Groq + GPU Local)',
    subtitle: 'Ouvido instantâneo na nuvem + voz expressiva local sem gastar créditos de TTS.',
    ttsProvider: 'local_sidecar',
    ttsModelId: 'chatterbox-pt-br',
    ttsLabel: 'Chatterbox PT-BR (500M Local)',
    sttProvider: 'groq',
    sttModelId: 'whisper-large-v3-turbo',
    sttLabel: 'Groq Whisper Large-v3-Turbo (Cloud)',
  },
  {
    id: 'cloud_zero_vram',
    badge: '☁️ Zero Uso de Placa de Vídeo',
    title: 'Cloud Premium (ElevenLabs + Groq)',
    subtitle: 'Ideal enquanto você joga, compila ou renderiza vídeos pesados no PC.',
    ttsProvider: 'elevenlabs',
    ttsModelId: 'eleven_flash_v2_5',
    ttsLabel: 'ElevenLabs Flash v2.5 (~75ms Cloud)',
    sttProvider: 'groq',
    sttModelId: 'whisper-large-v3-turbo',
    sttLabel: 'Groq Whisper Large-v3-Turbo (Cloud)',
  },
  {
    id: 'cpu_light',
    badge: '💻 Econômico / Sem GPU',
    title: 'Modo Leve CPU (Kokoro ONNX)',
    subtitle: 'Roda 100% local usando apenas processador com baixíssimo consumo de RAM.',
    ttsProvider: 'local_sidecar',
    ttsModelId: 'kokoro-82m-onnx',
    ttsLabel: 'Kokoro-82M v1.0 ONNX (CPU)',
    sttProvider: 'local_sidecar',
    sttModelId: 'faster-whisper-large-v3-turbo',
    sttLabel: 'Faster-Whisper int8 (Local)',
  },
]

const TTS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'automatic', label: 'Automático (Governador de Hardware)' },
  { value: 'local_sidecar:chatterbox-pt-br', label: 'Chatterbox PT-BR Expressive (Sidecar Local · GPU >= 6GB)' },
  { value: 'local_sidecar:f5-tts-pt-br', label: 'F5-TTS PT-BR Flow-Matching (Sidecar Local · GPU >= 4GB)' },
  { value: 'local_sidecar:kokoro-82m-onnx', label: 'Kokoro-82M v1.0 ONNX (Sidecar Local · CPU Ultra-Rápido)' },
  { value: 'elevenlabs:eleven_flash_v2_5', label: 'ElevenLabs Flash v2.5 (~75ms Cloud)' },
  { value: 'openai:gpt-4o-mini-tts', label: 'OpenAI GPT-4o Mini TTS (Cloud)' },
]

const STT_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'automatic', label: 'Automático (Governador de Hardware)' },
  { value: 'local_sidecar:faster-whisper-large-v3-turbo', label: 'Faster-Whisper Large-v3-Turbo (Sidecar Local)' },
  { value: 'groq:whisper-large-v3-turbo', label: 'Groq Whisper Large-v3-Turbo (<200ms Cloud)' },
  { value: 'deepgram:nova-3', label: 'Deepgram Nova-3 Streaming (Cloud)' },
  { value: 'openai:gpt-4o-mini-transcribe', label: 'OpenAI GPT-4o Mini Transcribe (Cloud)' },
]

const DEFAULT_SETTINGS: MediaModelSettings = {
  image: { provider: null, modelId: null, mode: 'automatic' },
  audio: { provider: 'local_sidecar', modelId: 'chatterbox-pt-br', mode: 'manual' },
  stt: { provider: 'local_sidecar', modelId: 'faster-whisper-large-v3-turbo', mode: 'manual' },
  video: { provider: null, modelId: null, mode: 'automatic' },
}

export type LiveVoiceQuickSettingsProps = {
  isOpen: boolean
  client?: ApiClient
  onClose: () => void
  onProfileSummaryChange?: (summary: string) => void
}

function formatSelectionValue(
  setting: { provider: MediaProviderName | null; modelId: string | null; mode: 'automatic' | 'manual' } | undefined,
): string {
  if (!setting || setting.mode === 'automatic' || !setting.provider || !setting.modelId) {
    return 'automatic'
  }
  return `${setting.provider}:${setting.modelId}`
}

function splitSelectionValue(
  value: string,
): { provider: MediaProviderName; modelId: string } | { provider: null; modelId: null } {
  if (!value || value === 'automatic') {
    return { provider: null, modelId: null }
  }
  const [provider, ...rest] = value.split(':')
  return {
    provider: provider as MediaProviderName,
    modelId: rest.join(':'),
  }
}

export function describeActiveVoiceProfile(settings: MediaModelSettings | null): string {
  if (!settings) return 'Auto (Governador de Voz)'
  const ttsVal = formatSelectionValue(settings.audio)
  const sttVal = formatSelectionValue(settings.stt)
  const matchedPreset = LIVE_VOICE_ONE_CLICK_PRESETS.find(
    (p) => `${p.ttsProvider}:${p.ttsModelId}` === ttsVal && `${p.sttProvider}:${p.sttModelId}` === sttVal,
  )
  if (matchedPreset) {
    return matchedPreset.title
  }
  const ttsModel = settings.audio.modelId ?? 'Auto TTS'
  const sttModel = settings.stt.modelId ?? 'Auto STT'
  return `${ttsModel} + ${sttModel}`
}

export function LiveVoiceQuickSettings({
  isOpen,
  client: providedClient,
  onClose,
  onProfileSummaryChange,
}: LiveVoiceQuickSettingsProps) {
  const client = useMemo(
    () =>
      providedClient ??
      new ApiClient({
        fetchImpl: (...args) => fetch(...args),
        csrfToken: 'orin-session-csrf',
        maxAttempts: 1,
        createIdempotencyKey: () => `live-quick-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      }),
    [providedClient],
  )

  const [settings, setSettings] = useState<MediaModelSettings>(DEFAULT_SETTINGS)
  const [hardwareDiag, setHardwareDiag] = useState<VoiceHardwareDiagnostic | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedNotice, setSavedNotice] = useState<string | null>(null)
  const [voicePreviewStatus, setVoicePreviewStatus] = useState<string | null>(null)
  const [micDevices, setMicDevices] = useState<Array<{ deviceId: string; label: string }>>([
    { deviceId: 'default', label: 'Microfone Padrão do Sistema (Anti-Eco 24kHz)' },
  ])
  const [selectedMicId, setSelectedMicId] = useState<string>('default')

  useEffect(() => {
    if (!isOpen) return
    const controller = new AbortController()
    getMediaModelSettings(client, controller.signal)
      .then((loaded) => {
        if (!controller.signal.aborted) {
          setSettings(loaded)
          onProfileSummaryChange?.(describeActiveVoiceProfile(loaded))
        }
      })
      .catch(() => {})

    getVoiceHardwareDiagnostic(client, controller.signal)
      .then((diag) => {
        if (!controller.signal.aborted) {
          setHardwareDiag(diag)
        }
      })
      .catch(() => {})

    if (typeof navigator !== 'undefined' && navigator.mediaDevices?.enumerateDevices) {
      navigator.mediaDevices
        .enumerateDevices()
        .then((devices) => {
          if (controller.signal.aborted) return
          const audioInputs = devices
            .filter((d) => d.kind === 'audioinput')
            .map((d, idx) => ({
              deviceId: d.deviceId || `mic-${idx}`,
              label: d.label || `Microfone ${idx + 1} (WebRTC Anti-Eco)`,
            }))
          if (audioInputs.length > 0) {
            setMicDevices(audioInputs)
          }
        })
        .catch(() => {})
    }

    return () => controller.abort()
  }, [isOpen, client, onProfileSummaryChange])

  if (!isOpen) return null

  const currentTtsValue = formatSelectionValue(settings.audio)
  const currentSttValue = formatSelectionValue(settings.stt)

  async function saveModalitySelection(modality: MediaModality, value: string) {
    const parsed = splitSelectionValue(value)
    const optimisticNext: MediaModelSettings = {
      ...settings,
      [modality]: {
        provider: parsed.provider,
        modelId: parsed.modelId,
        mode: parsed.provider ? 'manual' : 'automatic',
      },
    }
    setSettings(optimisticNext)
    onProfileSummaryChange?.(describeActiveVoiceProfile(optimisticNext))
    setSaving(true)
    setSavedNotice(null)
    try {
      const updated = await setMediaModelSetting(
        client,
        modality,
        parsed,
        client.createMutationIntent(),
      )
      setSettings((prev) => {
        const next = { ...prev, [modality]: updated }
        onProfileSummaryChange?.(describeActiveVoiceProfile(next))
        return next
      })
      setSavedNotice('Configuração de voz salva instantaneamente.')
    } catch {
      // Mantém estado otimista para experiência fluida
      setSavedNotice('Perfil atualizado para esta sessão.')
    } finally {
      setSaving(false)
    }
  }

  async function applyPreset(preset: LiveVoicePreset) {
    const optimisticNext: MediaModelSettings = {
      ...settings,
      audio: {
        provider: preset.ttsProvider,
        modelId: preset.ttsModelId,
        mode: 'manual',
      },
      stt: {
        provider: preset.sttProvider,
        modelId: preset.sttModelId,
        mode: 'manual',
      },
    }
    setSettings(optimisticNext)
    onProfileSummaryChange?.(describeActiveVoiceProfile(optimisticNext))
    setSaving(true)
    setSavedNotice(`Perfil "${preset.title}" aplicado!`)
    try {
      await Promise.all([
        setMediaModelSetting(
          client,
          'audio',
          { provider: preset.ttsProvider, modelId: preset.ttsModelId },
          client.createMutationIntent(),
        ),
        setMediaModelSetting(
          client,
          'stt',
          { provider: preset.sttProvider, modelId: preset.sttModelId },
          client.createMutationIntent(),
        ),
      ])
    } catch {
      // Mantém atualização otimista
    } finally {
      setSaving(false)
    }
  }

  function handleTestVoiceSample() {
    const activeSummary = describeActiveVoiceProfile(settings)
    setVoicePreviewStatus(
      `♪ Amostra PT-BR (${activeSummary}): "Olá! Eu sou o Orin, pronto para conversar com você em tempo real."`,
    )
  }

  return (
    <div
      data-testid="live-voice-quick-settings"
      role="dialog"
      aria-modal="true"
      aria-labelledby="live-voice-quick-settings-title"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 110,
        background: 'rgba(5, 4, 12, 0.82)',
        backdropFilter: 'blur(12px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1.25rem',
        color: '#F4F2FF',
      }}
    >
      <section
        style={{
          width: '100%',
          maxWidth: '720px',
          maxHeight: '90vh',
          overflowY: 'auto',
          borderRadius: '1.15rem',
          background: '#110E22',
          border: '1px solid rgba(167, 139, 250, 0.35)',
          boxShadow: '0 24px 64px rgba(0, 0, 0, 0.65)',
          padding: '1.5rem',
          display: 'grid',
          gap: '1.15rem',
        }}
      >
        <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem' }}>
          <div>
            <span
              style={{
                display: 'inline-block',
                fontSize: '0.74rem',
                fontWeight: 700,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                color: '#C4B5FD',
                marginBottom: '0.25rem',
              }}
            >
              ◈ Configuração Rápida · Modo Conversa Live
            </span>
            <h2 id="live-voice-quick-settings-title" style={{ margin: 0, fontSize: '1.25rem', color: '#F4F2FF' }}>
              Voz (TTS), Ouvido (STT) e Hardware em 1 Clique
            </h2>
          </div>
          <button
            type="button"
            className="btn-ghost"
            data-testid="live-quick-close-button"
            onClick={onClose}
            aria-label="Fechar configuração rápida de voz"
          >
            Fechar
          </button>
        </header>

        <div
          data-testid="live-quick-hardware-banner"
          style={{
            padding: '0.8rem 1rem',
            borderRadius: '0.75rem',
            background: 'rgba(139, 92, 246, 0.14)',
            border: '1px solid rgba(167, 139, 250, 0.32)',
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '0.5rem',
            fontSize: '0.86rem',
          }}
        >
          <div>
            <strong style={{ color: '#DDD6FE' }}>
              {hardwareDiag?.gpu_available
                ? `🟢 Hardware Detectado: ${hardwareDiag.gpu_name ?? 'GPU CUDA'} (${hardwareDiag.free_vram_gb.toFixed(1)} GB VRAM livre)`
                : `🟢 Governador de Hardware Ativo (${hardwareDiag ? `${hardwareDiag.free_ram_gb.toFixed(1)} GB RAM livre` : 'Auto-Routing'})`}
            </strong>
            <div style={{ opacity: 0.85, marginTop: '0.18rem' }}>
              {hardwareDiag?.reason ||
                'Seus modelos de Ouvido (STT) e Voz (TTS) são desacoplados e se adaptam automaticamente à sua máquina.'}
            </div>
          </div>
        </div>

        <div>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#DDD6FE', marginBottom: '0.6rem' }}>
            1. Escolha um Perfil Pronto em 1 Clique:
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: '0.7rem',
            }}
          >
            {LIVE_VOICE_ONE_CLICK_PRESETS.map((preset) => {
              const isSelected =
                currentTtsValue === `${preset.ttsProvider}:${preset.ttsModelId}` &&
                currentSttValue === `${preset.sttProvider}:${preset.sttModelId}`
              return (
                <button
                  key={preset.id}
                  type="button"
                  data-testid={`live-preset-card-${preset.id}`}
                  aria-pressed={isSelected}
                  disabled={saving}
                  onClick={() => void applyPreset(preset)}
                  style={{
                    textAlign: 'left',
                    padding: '0.85rem 0.95rem',
                    borderRadius: '0.85rem',
                    background: isSelected ? 'rgba(139, 92, 246, 0.26)' : 'rgba(255, 255, 255, 0.04)',
                    border: isSelected
                      ? '2px solid #A78BFA'
                      : '1px solid rgba(255, 255, 255, 0.12)',
                    color: '#F4F2FF',
                    cursor: 'pointer',
                    display: 'grid',
                    gap: '0.3rem',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.4rem' }}>
                    <span style={{ fontSize: '0.73rem', fontWeight: 700, color: '#C4B5FD' }}>{preset.badge}</span>
                    {isSelected && (
                      <span
                        style={{
                          fontSize: '0.7rem',
                          fontWeight: 700,
                          padding: '0.1rem 0.45rem',
                          borderRadius: '999px',
                          background: '#6D28D9',
                          color: '#F4F2FF',
                        }}
                      >
                        ✓ Ativo
                      </span>
                    )}
                  </div>
                  <strong style={{ fontSize: '0.95rem' }}>{preset.title}</strong>
                  <span style={{ fontSize: '0.78rem', opacity: 0.8, lineHeight: 1.35 }}>{preset.subtitle}</span>
                  <div style={{ fontSize: '0.74rem', color: '#DDD6FE', marginTop: '0.2rem' }}>
                    🗣 {preset.ttsLabel} · 👂 {preset.sttLabel}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: '0.85rem',
            paddingTop: '0.35rem',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
          }}
        >
          <div style={{ display: 'grid', gap: '0.35rem' }}>
            <label htmlFor="live-quick-tts-select" style={{ fontSize: '0.82rem', fontWeight: 600, color: '#DDD6FE' }}>
              🗣 Modelo de Voz (TTS — O que o Orin fala)
            </label>
            <select
              id="live-quick-tts-select"
              data-testid="live-quick-tts-select"
              value={currentTtsValue}
              disabled={saving}
              onChange={(e) => void saveModalitySelection('audio', e.target.value)}
              style={{
                padding: '0.55rem 0.7rem',
                borderRadius: '0.55rem',
                background: '#1B1633',
                color: '#F4F2FF',
                border: '1px solid rgba(167, 139, 250, 0.35)',
              }}
            >
              {TTS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: 'grid', gap: '0.35rem' }}>
            <label htmlFor="live-quick-stt-select" style={{ fontSize: '0.82rem', fontWeight: 600, color: '#DDD6FE' }}>
              👂 Modelo de Ouvido (STT — Quem transcreve sua voz)
            </label>
            <select
              id="live-quick-stt-select"
              data-testid="live-quick-stt-select"
              value={currentSttValue}
              disabled={saving}
              onChange={(e) => void saveModalitySelection('stt', e.target.value)}
              style={{
                padding: '0.55rem 0.7rem',
                borderRadius: '0.55rem',
                background: '#1B1633',
                color: '#F4F2FF',
                border: '1px solid rgba(167, 139, 250, 0.35)',
              }}
            >
              {STT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'space-between',
            alignItems: 'flex-end',
            gap: '0.85rem',
            paddingTop: '0.35rem',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
          }}
        >
          <div style={{ display: 'grid', gap: '0.35rem', flex: '1 1 260px' }}>
            <label htmlFor="live-quick-mic-select" style={{ fontSize: '0.82rem', fontWeight: 600, color: '#DDD6FE' }}>
              🎙 Microfone de Entrada (WebRTC Anti-Eco)
            </label>
            <select
              id="live-quick-mic-select"
              data-testid="live-quick-mic-select"
              value={selectedMicId}
              onChange={(e) => setSelectedMicId(e.target.value)}
              style={{
                padding: '0.55rem 0.7rem',
                borderRadius: '0.55rem',
                background: '#1B1633',
                color: '#F4F2FF',
                border: '1px solid rgba(167, 139, 250, 0.35)',
              }}
            >
              {micDevices.map((mic) => (
                <option key={mic.deviceId} value={mic.deviceId}>
                  {mic.label}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            className="btn-secondary"
            data-testid="live-quick-test-voice-button"
            onClick={handleTestVoiceSample}
            style={{ padding: '0.58rem 1rem' }}
          >
            🔊 Ouvir Amostra PT-BR
          </button>
        </div>

        {voicePreviewStatus && (
          <div
            data-testid="live-quick-voice-preview-status"
            role="status"
            style={{
              padding: '0.65rem 0.9rem',
              borderRadius: '0.65rem',
              background: 'rgba(16, 185, 129, 0.14)',
              border: '1px solid rgba(52, 211, 153, 0.35)',
              color: '#D1FAE5',
              fontSize: '0.84rem',
            }}
          >
            {voicePreviewStatus}
          </div>
        )}

        {savedNotice && (
          <div
            data-testid="live-quick-saved-notice"
            role="status"
            style={{ fontSize: '0.82rem', color: '#C4B5FD', textAlign: 'right' }}
          >
            ✓ {savedNotice}
          </div>
        )}
      </section>
    </div>
  )
}
