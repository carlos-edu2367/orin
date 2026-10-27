import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiClient, type MutationIntent } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError } from '../../api/errors'
import {
  PROVIDER_NAMES,
  listProviderModels,
  getMediaModelSettings,
  setMediaModelSetting,
  getVoiceHardwareDiagnostic,
  type MediaModality,
  type MediaModelSettings,
  type MediaProviderName,
  type ProviderModel,
  type VoiceHardwareDiagnostic,
} from '../../api/providers'

const AUTOMATIC_VALUE = 'automatic'

type MediaModelsSettingProps = {
  client?: ApiClient
  bootstrap?: BrowserSessionBootstrap
}

type ModalityMeta = {
  id: MediaModality
  title: string
  selectLabel: string
  searchLabel: string
  searchPlaceholder: string
  description: string
}

type BuiltinVoicePreset = {
  provider: MediaProviderName
  model_id: string
  display_name: string
}

const VOICE_PRESETS_BY_MODALITY: Partial<Record<MediaModality, ReadonlyArray<BuiltinVoicePreset>>> = {
  audio: [
    {
      provider: 'local_sidecar',
      model_id: 'chatterbox-pt-br',
      display_name: 'Chatterbox PT-BR Expressive (Sidecar Local · GPU >= 6GB)',
    },
    {
      provider: 'local_sidecar',
      model_id: 'f5-tts-pt-br',
      display_name: 'F5-TTS PT-BR Flow-Matching (Sidecar Local · GPU >= 4GB)',
    },
    {
      provider: 'local_sidecar',
      model_id: 'kokoro-82m-onnx',
      display_name: 'Kokoro-82M v1.0 ONNX (Sidecar Local · CPU Ultra-Rápido)',
    },
    {
      provider: 'elevenlabs',
      model_id: 'eleven_flash_v2_5',
      display_name: 'ElevenLabs Flash v2.5 (~75ms Cloud)',
    },
    {
      provider: 'openai',
      model_id: 'gpt-4o-mini-tts',
      display_name: 'OpenAI GPT-4o Mini TTS (Cloud)',
    },
  ],
  stt: [
    {
      provider: 'local_sidecar',
      model_id: 'faster-whisper-large-v3-turbo',
      display_name: 'Faster-Whisper Large-v3-Turbo (Sidecar Local · GPU/CPU int8)',
    },
    {
      provider: 'groq',
      model_id: 'whisper-large-v3-turbo',
      display_name: 'Groq Whisper Large-v3-Turbo (<200ms Cloud)',
    },
    {
      provider: 'deepgram',
      model_id: 'nova-3',
      display_name: 'Deepgram Nova-3 Streaming (Cloud)',
    },
    {
      provider: 'openai',
      model_id: 'gpt-4o-mini-transcribe',
      display_name: 'OpenAI GPT-4o Mini Transcribe (Cloud)',
    },
  ],
}

const MODALITIES: ReadonlyArray<ModalityMeta> = [
  {
    id: 'image',
    title: 'Imagem',
    selectLabel: 'Modelo padrão de imagem',
    searchLabel: 'Filtrar modelos de imagem',
    searchPlaceholder: 'Ex.: qwen 2.1, flux, gpt-image…',
    description: 'Usado em generate_image mesmo quando a conversa roda em outro provider (como NVIDIA ou Anthropic).',
  },
  {
    id: 'audio',
    title: 'Áudio e voz — TTS',
    selectLabel: 'Modelo padrão de áudio',
    searchLabel: 'Filtrar modelos de áudio',
    searchPlaceholder: 'Ex.: chatterbox, f5-tts, kokoro, eleven v3…',
    description: 'Usado na Conversa Live, no Orin Friend e em generate_audio para síntese de voz (TTS) expressiva.',
  },
  {
    id: 'stt',
    title: 'Ouvido e transcrição — STT',
    selectLabel: 'Modelo padrão de transcrição (STT)',
    searchLabel: 'Filtrar modelos de transcrição',
    searchPlaceholder: 'Ex.: faster-whisper, groq, deepgram…',
    description: 'Usado para transcrição em tempo real (STT) do microfone durante o modo Conversa Live.',
  },
  {
    id: 'video',
    title: 'Vídeo',
    selectLabel: 'Modelo padrão de vídeo',
    searchLabel: 'Filtrar modelos de vídeo',
    searchPlaceholder: 'Ex.: veo, sora, wan, seedance…',
    description: 'Usado em generate_video para geração de clipes a partir de prompts.',
  },
]

const DEFAULT_SETTINGS: MediaModelSettings = {
  image: { provider: null, modelId: null, mode: 'automatic' },
  audio: { provider: null, modelId: null, mode: 'automatic' },
  stt: { provider: null, modelId: null, mode: 'automatic' },
  video: { provider: null, modelId: null, mode: 'automatic' },
}

export function MediaModelsSetting({ client: providedClient, bootstrap: providedBootstrap }: MediaModelsSettingProps = {}) {
  const client = useMemo(
    () =>
      providedClient ??
      new ApiClient({
        fetchImpl: (...args) => fetch(...args),
        csrfToken: 'orin-session-csrf',
        maxAttempts: 1,
        createIdempotencyKey: () => `media-intent-${Date.now()}`,
      }),
    [providedClient],
  )
  const bootstrap: BrowserSessionBootstrap = useMemo(() => {
    if (providedBootstrap) return providedBootstrap
    if (!providedClient) return { status: 'ready', csrfToken: 'orin-session-csrf' }
    return typeof document === 'undefined'
      ? { status: 'missing_csrf' as const }
      : readBrowserSessionBootstrap(document)
  }, [providedBootstrap, providedClient])

  const [models, setModels] = useState<ProviderModel[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [settings, setSettings] = useState<MediaModelSettings | null>(null)
  const [hardwareDiag, setHardwareDiag] = useState<VoiceHardwareDiagnostic | null>(null)
  const [queries, setQueries] = useState<Record<MediaModality, string>>({
    image: '',
    audio: '',
    stt: '',
    video: '',
  })
  const [pendingModality, setPendingModality] = useState<MediaModality | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const intents = useRef(new Map<MediaModality, MutationIntent>())

  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled(PROVIDER_NAMES.map((provider) => listProviderModels(client, provider, controller.signal))).then(
      (results) => {
        if (controller.signal.aborted) return
        const items = results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
        setModels(items)
        setModelsLoaded(true)
      },
    )
    getMediaModelSettings(client, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setSettings(value)
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setSettings(DEFAULT_SETTINGS)
      })
    getVoiceHardwareDiagnostic(client, controller.signal)
      .then((diag) => {
        if (!controller.signal.aborted && (diag.reason || diag.gpu_name || diag.free_vram_gb > 0)) {
          setHardwareDiag(diag)
        }
      })
      .catch(() => {
        // Diagnóstico de hardware opcional
      })
    return () => controller.abort()
  }, [client])

  const modelsByModality = useMemo(() => {
    const map: Record<MediaModality, ProviderModel[]> = { image: [], audio: [], stt: [], video: [] }
    for (const model of models) {
      for (const mod of ['image', 'audio', 'video'] as const) {
        if (model.output_modalities.includes(mod)) {
          map[mod].push(model)
        }
      }
      if (model.input_modalities.includes('audio')) {
        map.stt.push(model)
      }
    }
    return map
  }, [models])

  async function onChange(modality: MediaModality, value: string) {
    if (pendingModality !== null || bootstrap.status === 'missing_csrf') return
    const optimisticNext =
      value === AUTOMATIC_VALUE
        ? { provider: null, modelId: null, mode: 'automatic' as const }
        : { ...splitValue(value), mode: 'manual' as const }
    setSettings((prev) => ({ ...(prev ?? DEFAULT_SETTINGS), [modality]: optimisticNext }))
    setPendingModality(modality)
    setError(null)
    let intent = intents.current.get(modality)
    if (!intent) {
      intent = client.createMutationIntent()
      intents.current.set(modality, intent)
    }
    try {
      const next =
        value === AUTOMATIC_VALUE
          ? await setMediaModelSetting(client, modality, { provider: null, modelId: null }, intent)
          : await setMediaModelSetting(client, modality, splitValue(value), intent)
      intents.current.delete(modality)
      setSettings((prev) => ({ ...(prev ?? DEFAULT_SETTINGS), [modality]: next }))
    } catch (err) {
      setError(toApiError(err))
    } finally {
      setPendingModality(null)
    }
  }

  async function onSaveAllSettings() {
    if (!settings || bootstrap.status === 'missing_csrf') return
    setError(null)
    try {
      await fetch('/v1/settings/media-models', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image: {
            provider: settings.image.provider,
            model_id: settings.image.modelId,
            mode: settings.image.mode,
          },
          audio: {
            provider: settings.audio.provider,
            model_id: settings.audio.modelId,
            mode: settings.audio.mode,
          },
          stt: {
            provider: settings.stt.provider,
            model_id: settings.stt.modelId,
            mode: settings.stt.mode,
          },
          video: {
            provider: settings.video.provider,
            model_id: settings.video.modelId,
            mode: settings.video.mode,
          },
        }),
      })
    } catch (err) {
      setError(toApiError(err))
    }
  }

  return (
    <section className="vision-model-setting media-models-setting" aria-labelledby="media-models-setting-title">
      <h2 id="media-models-setting-title">Geração de mídia e Voz Live</h2>
      <p className="vision-model-setting__hint">
        Defina o provider e o modelo padrão para criar imagens, síntese de voz (TTS), transcrição de ouvido (STT) e
        vídeos. No modo Automático, o VoiceHardwareGovernor seleciona o melhor modelo local ou cloud sem travar seu PC.
      </p>

      {hardwareDiag && (
        <div
          className="voice-hardware-governor-card"
          data-testid="voice-hardware-governor-card"
          style={{
            marginBottom: '1rem',
            padding: '0.85rem 1rem',
            borderRadius: '0.75rem',
            background: 'rgba(139, 92, 246, 0.08)',
            border: '1px solid rgba(139, 92, 246, 0.28)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
            <strong>◈ Governador de Hardware de Voz (Auto-Routing)</strong>
            <span>
              {hardwareDiag.gpu_available
                ? `${hardwareDiag.gpu_name ?? 'GPU'} · ${hardwareDiag.free_vram_gb.toFixed(1)} GB VRAM livre`
                : `CPU · ${hardwareDiag.free_ram_gb.toFixed(1)} GB RAM livre`}
            </span>
          </div>
          {hardwareDiag.reason && (
            <p style={{ margin: '0.35rem 0 0', fontSize: '0.85rem', opacity: 0.88 }}>{hardwareDiag.reason}</p>
          )}
        </div>
      )}

      <div className="media-models-setting__rows">
        {MODALITIES.map((meta) => {
          const currentSetting = settings?.[meta.id]
          const currentValue =
            !currentSetting || currentSetting.mode === 'automatic'
              ? AUTOMATIC_VALUE
              : `${currentSetting.provider ?? ''}:${currentSetting.modelId ?? ''}`
          const query = queries[meta.id]
          const words = query.toLowerCase().split(/\s+/).filter(Boolean)
          const allForMod = modelsByModality[meta.id]
          const builtinPresets = VOICE_PRESETS_BY_MODALITY[meta.id] ?? []
          const filtered = allForMod.filter((item) => {
            const key = `${item.provider}:${item.model_id}`
            if (key === currentValue) return true
            if (words.length === 0) return true
            const haystack = `${item.display_name} ${item.model_id} ${providerLabel(item.provider)}`.toLowerCase()
            return words.every((word) => haystack.includes(word))
          })
          const filteredPresets = builtinPresets.filter((preset) => {
            const key = `${preset.provider}:${preset.model_id}`
            if (key === currentValue) return true
            if (words.length === 0) return true
            const haystack = `${preset.display_name} ${preset.model_id} ${providerLabel(preset.provider)}`.toLowerCase()
            return words.every((word) => haystack.includes(word))
          })
          const favorites = filtered.filter((item) => item.is_favorite)
          const others = filtered.filter((item) => !item.is_favorite)
          const isSaving = pendingModality === meta.id

          return (
            <div key={meta.id} className="media-models-setting__row" data-testid={`media-model-row-${meta.id}`}>
              <div className="media-models-setting__row-header">
                <strong>{meta.title}</strong>
                <span className="media-models-setting__badge" data-mode={currentSetting?.mode ?? 'automatic'}>
                  {currentSetting?.mode === 'manual' && currentSetting.provider && currentSetting.modelId
                    ? `${providerLabel(currentSetting.provider)} · ${currentSetting.modelId}`
                    : 'Automático'}
                </span>
              </div>
              <p className="media-models-setting__row-desc">{meta.description}</p>
              <div className="media-models-setting__controls">
                <div className="media-models-setting__field">
                  <label htmlFor={`media-model-search-${meta.id}`}>{meta.searchLabel}</label>
                  <input
                    id={`media-model-search-${meta.id}`}
                    type="search"
                    value={query}
                    placeholder={meta.searchPlaceholder}
                    autoComplete="off"
                    onChange={(event) =>
                      setQueries((prev) => ({
                        ...prev,
                        [meta.id]: event.target.value,
                      }))
                    }
                  />
                </div>
                <div className="media-models-setting__field">
                  <label htmlFor={`media-model-select-${meta.id}`}>{meta.selectLabel}</label>
                  <select
                    id={`media-model-select-${meta.id}`}
                    value={currentValue}
                    disabled={isSaving || !settings || bootstrap.status === 'missing_csrf'}
                    onChange={(event) => void onChange(meta.id, event.target.value)}
                  >
                    <option value={AUTOMATIC_VALUE}>Automático (favoritos dos providers ativos)</option>
                    {currentSetting?.mode === 'manual' &&
                      currentSetting.provider &&
                      currentSetting.modelId &&
                      !filtered.some((m) => `${m.provider}:${m.model_id}` === currentValue) &&
                      !filteredPresets.some((p) => `${p.provider}:${p.model_id}` === currentValue) && (
                        <option value={currentValue}>
                          {currentSetting.modelId} ({providerLabel(currentSetting.provider)})
                        </option>
                      )}
                    {filteredPresets.length > 0 && (
                      <optgroup label="Voz Live & Sidecar Local">
                        {filteredPresets.map((preset) => (
                          <option
                            key={`${preset.provider}:${preset.model_id}`}
                            value={`${preset.provider}:${preset.model_id}`}
                          >
                            {preset.display_name} ({providerLabel(preset.provider)})
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {favorites.length > 0 && (
                      <optgroup label="Favoritos">
                        {favorites.map((model) => (
                          <option
                            key={`${model.provider}:${model.model_id}`}
                            value={`${model.provider}:${model.model_id}`}
                          >
                            {model.display_name} ({providerLabel(model.provider)})
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {others.length > 0 && (
                      <optgroup label={favorites.length > 0 ? 'Todos os modelos compatíveis' : 'Modelos compatíveis'}>
                        {others.map((model) => (
                          <option
                            key={`${model.provider}:${model.model_id}`}
                            value={`${model.provider}:${model.model_id}`}
                          >
                            {model.display_name} ({providerLabel(model.provider)})
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </div>
              </div>
              {modelsLoaded && allForMod.length === 0 && builtinPresets.length === 0 && (
                <p role="status">Nenhum modelo de {meta.title.toLowerCase()} encontrado no catálogo ainda.</p>
              )}
            </div>
          )
        })}
      </div>

      <div style={{ marginTop: '1rem', display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="button"
          className="btn-secondary"
          data-testid="save-media-models-button"
          disabled={!settings || bootstrap.status === 'missing_csrf'}
          onClick={() => void onSaveAllSettings()}
        >
          Salvar configurações de voz e mídia
        </button>
      </div>

      {bootstrap.status === 'missing_csrf' && (
        <p role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de trocar o modelo.</p>
      )}
      {error && <p role="alert">Não foi possível salvar o modelo padrão de mídia agora.</p>}
    </section>
  )
}

function splitValue(value: string): { provider: MediaProviderName; modelId: string } {
  const [provider, ...rest] = value.split(':')
  return { provider: provider as MediaProviderName, modelId: rest.join(':') }
}

function providerLabel(provider: MediaProviderName): string {
  switch (provider) {
    case 'openai':
      return 'OpenAI'
    case 'anthropic':
      return 'Anthropic'
    case 'openrouter':
      return 'OpenRouter'
    case 'nvidia':
      return 'NVIDIA NIM'
    case 'opencode':
      return 'OpenCode'
    case 'omniroute':
      return 'OmniRoute'
    case 'ollama':
      return 'Ollama'
    case 'local_sidecar':
      return 'Sidecar Local'
    case 'groq':
      return 'Groq'
    case 'elevenlabs':
      return 'ElevenLabs'
    case 'deepgram':
      return 'Deepgram'
    case 'cartesia':
      return 'Cartesia'
  }
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError({
    status: 0,
    code: 'request_failed',
    category: 'INTERNAL',
    messageKey: 'request_failed',
    retryable: false,
  })
}
