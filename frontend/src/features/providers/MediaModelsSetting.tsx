import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiClient, type MutationIntent } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError } from '../../api/errors'
import {
  PROVIDER_NAMES,
  listProviderModels,
  getMediaModelSettings,
  setMediaModelSetting,
  type MediaModality,
  type MediaModelSettings,
  type ProviderModel,
  type ProviderName,
} from '../../api/providers'

const AUTOMATIC_VALUE = 'automatic'

type MediaModelsSettingProps = {
  client: ApiClient
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
    title: 'Áudio e voz',
    selectLabel: 'Modelo padrão de áudio',
    searchLabel: 'Filtrar modelos de áudio',
    searchPlaceholder: 'Ex.: eleven v3, tts-1, kokoro…',
    description: 'Usado em generate_audio para locução, síntese de voz (TTS) e efeitos sonoros.',
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
  video: { provider: null, modelId: null, mode: 'automatic' },
}

export function MediaModelsSetting({ client, bootstrap: providedBootstrap }: MediaModelsSettingProps) {
  const bootstrap =
    providedBootstrap ??
    (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))
  const [models, setModels] = useState<ProviderModel[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [settings, setSettings] = useState<MediaModelSettings | null>(null)
  const [queries, setQueries] = useState<Record<MediaModality, string>>({ image: '', audio: '', video: '' })
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
    return () => controller.abort()
  }, [client])

  const modelsByModality = useMemo(() => {
    const map: Record<MediaModality, ProviderModel[]> = { image: [], audio: [], video: [] }
    for (const model of models) {
      for (const mod of ['image', 'audio', 'video'] as const) {
        if (model.output_modalities.includes(mod)) {
          map[mod].push(model)
        }
      }
    }
    return map
  }, [models])

  async function onChange(modality: MediaModality, value: string) {
    if (pendingModality !== null || bootstrap.status === 'missing_csrf') return
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

  return (
    <section className="vision-model-setting media-models-setting" aria-labelledby="media-models-setting-title">
      <h2 id="media-models-setting-title">Geração de mídia</h2>
      <p className="vision-model-setting__hint">
        Defina o provider e o modelo padrão para criar imagens, áudios e vídeos. O modelo escolhido aqui é autorizado
        automaticamente e usado mesmo quando o chat está em outro provider — além disso, outros favoritos dos seus
        providers ativos continuam disponíveis se você pedir no prompt.
      </p>

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
          const filtered = allForMod.filter((item) => {
            const key = `${item.provider}:${item.model_id}`
            if (key === currentValue) return true
            if (words.length === 0) return true
            const haystack = `${item.display_name} ${item.model_id} ${providerLabel(item.provider)}`.toLowerCase()
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
                      !filtered.some((m) => `${m.provider}:${m.model_id}` === currentValue) && (
                        <option value={currentValue}>
                          {currentSetting.modelId} ({providerLabel(currentSetting.provider)})
                        </option>
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
              {modelsLoaded && allForMod.length === 0 && (
                <p role="status">Nenhum modelo de {meta.title.toLowerCase()} encontrado no catálogo ainda.</p>
              )}
            </div>
          )
        })}
      </div>

      {bootstrap.status === 'missing_csrf' && (
        <p role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de trocar o modelo.</p>
      )}
      {error && <p role="alert">Não foi possível salvar o modelo padrão de mídia agora.</p>}
    </section>
  )
}

function splitValue(value: string): { provider: ProviderName; modelId: string } {
  const [provider, ...rest] = value.split(':')
  return { provider: provider as ProviderName, modelId: rest.join(':') }
}

function providerLabel(provider: ProviderName): string {
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
