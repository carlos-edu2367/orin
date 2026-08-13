import { useEffect, useRef, useState } from 'react'
import { ApiClient, type MutationIntent } from '../../api/client'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError } from '../../api/errors'
import {
  PROVIDER_NAMES,
  listProviderModels,
  getVisionModelSetting,
  setVisionModelSetting,
  type ProviderModel,
  type ProviderName,
  type VisionModelSetting as VisionModelSettingValue,
} from '../../api/providers'

const AUTOMATIC_VALUE = 'automatic'

type VisionModelSettingProps = {
  client: ApiClient
  bootstrap: BrowserSessionBootstrap
}

/**
 * Which model reads an attached image or scanned PDF when the conversation's
 * own model cannot see (worker precedence in `src/agentos/reading/selection.py`:
 * this override first, then the turn's own provider, then a local Ollama, then
 * anything else). The catalog is fetched per provider (there is no combined
 * `/v1/models`) and filtered down to models the gateway's `input_modalities`
 * marks as accepting `image` — never by guessing from the model's name.
 */
export function VisionModelSetting({ client, bootstrap }: VisionModelSettingProps) {
  const [models, setModels] = useState<ProviderModel[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [setting, setSetting] = useState<VisionModelSettingValue | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const intent = useRef<MutationIntent | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled(PROVIDER_NAMES.map((provider) => listProviderModels(client, provider, controller.signal)))
      .then((results) => {
        if (controller.signal.aborted) return
        const items = results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
        setModels(items.filter((item) => item.input_modalities.includes('image')))
        setModelsLoaded(true)
      })
    getVisionModelSetting(client, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setSetting(value) })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setSetting({ provider: null, modelId: null, mode: 'automatic' })
      })
    return () => controller.abort()
  }, [client])

  const currentValue = !setting || setting.mode === 'automatic' ? AUTOMATIC_VALUE : `${setting.provider ?? ''}:${setting.modelId ?? ''}`

  async function onChange(value: string) {
    if (pending || bootstrap.status === 'missing_csrf') return
    setPending(true)
    setError(null)
    if (!intent.current) intent.current = client.createMutationIntent()
    try {
      const next = value === AUTOMATIC_VALUE
        ? await setVisionModelSetting(client, { provider: null, modelId: null }, intent.current)
        : await setVisionModelSetting(client, splitValue(value), intent.current)
      intent.current = null
      setSetting(next)
    } catch (err) {
      setError(toApiError(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="vision-model-setting" aria-labelledby="vision-model-setting-title">
      <h2 id="vision-model-setting-title">Leitura visual</h2>
      <p className="vision-model-setting__hint">
        Quando o modelo da conversa não consegue ver, o arquivo anexado é enviado ao provider do modelo escolhido
        aqui para ser lido — por isso o modo automático prefere um Ollama local, mantendo o arquivo na sua máquina
        sempre que possível.
      </p>
      <label htmlFor="vision-model-select">Modelo de leitura visual</label>
      <select
        id="vision-model-select"
        value={currentValue}
        disabled={pending || !setting || bootstrap.status === 'missing_csrf'}
        onChange={(event) => void onChange(event.target.value)}
      >
        <option value={AUTOMATIC_VALUE}>Automático</option>
        {models.map((model) => (
          <option key={`${model.provider}:${model.model_id}`} value={`${model.provider}:${model.model_id}`}>
            {model.display_name} ({providerLabel(model.provider)})
          </option>
        ))}
      </select>
      {modelsLoaded && models.length === 0 && (
        <p role="status">Nenhum modelo com suporte a imagem disponível no catálogo ainda.</p>
      )}
      {bootstrap.status === 'missing_csrf' && (
        <p role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de trocar o modelo.</p>
      )}
      {error && <p role="alert">Não foi possível salvar o modelo de leitura visual agora.</p>}
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
    case 'omniroute':
      return 'OmniRoute'
    case 'ollama':
      return 'Ollama'
  }
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError({ status: 0, code: 'request_failed', category: 'INTERNAL', messageKey: 'request_failed', retryable: false })
}
