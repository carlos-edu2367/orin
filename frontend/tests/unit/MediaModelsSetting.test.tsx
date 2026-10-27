import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { MediaModelsSetting } from '../../src/features/providers/MediaModelsSetting'

afterEach(() => vi.restoreAllMocks())

function client(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function mediaModel(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    provider: 'openrouter',
    model_id: 'qwen/qwen-image-2.1-pro',
    display_name: 'Qwen Image 2.1 Pro',
    context_window: null,
    capabilities: [],
    input_modalities: ['text'],
    output_modalities: ['image'],
    pricing: null,
    is_favorite: false,
    refreshed_at: null,
    ...overrides,
  }
}

function fetchStub({
  models = {},
  settings = {
    image: { provider: null, model_id: null, mode: 'automatic' },
    audio: { provider: null, model_id: null, mode: 'automatic' },
    video: { provider: null, model_id: null, mode: 'automatic' },
  },
}: {
  models?: Record<string, unknown[]>
  settings?: Record<string, unknown>
} = {}) {
  return vi.fn<typeof fetch>((input, init) => {
    const url = String(input)
    const putMatch = /\/v1\/settings\/media-models\/(image|audio|video)$/.exec(url)
    if (putMatch && init?.method === 'PUT') {
      const modality = putMatch[1]
      const body = JSON.parse(String(init.body))
      return Promise.resolve(
        json({
          modality,
          provider: body.provider,
          model_id: body.model_id,
          mode: body.provider === null ? 'automatic' : 'manual',
        }),
      )
    }
    if (url.endsWith('/v1/settings/media-models')) {
      return Promise.resolve(json(settings))
    }
    const providerMatch = /\/v1\/providers\/([^/]+)\/models$/.exec(url)
    if (providerMatch) {
      return Promise.resolve(json({ items: models[providerMatch[1]] ?? [] }))
    }
    return Promise.resolve(json({}))
  })
}

describe('MediaModelsSetting', () => {
  it('lists compatible models per modality across providers and saves default selections', async () => {
    const fetchImpl = fetchStub({
      models: {
        openrouter: [
          mediaModel({
            provider: 'openrouter',
            model_id: 'qwen/qwen-image-2.1-pro',
            display_name: 'Qwen Image 2.1 Pro',
            output_modalities: ['image'],
            is_favorite: false,
          }),
          mediaModel({
            provider: 'openrouter',
            model_id: 'elevenlabs/eleven-v3',
            display_name: 'ElevenLabs: Eleven v3',
            output_modalities: ['audio'],
            is_favorite: true,
          }),
        ],
        openai: [
          mediaModel({
            provider: 'openai',
            model_id: 'gpt-image-1',
            display_name: 'GPT Image 1',
            output_modalities: ['image'],
            is_favorite: true,
          }),
        ],
      },
    })
    const user = userEvent.setup()
    render(<MediaModelsSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const imageSelect = await screen.findByLabelText('Modelo padrão de imagem')
    const audioSelect = await screen.findByLabelText('Modelo padrão de áudio')
    const videoSelect = await screen.findByLabelText('Modelo padrão de vídeo')

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /Qwen Image 2.1 Pro \(OpenRouter\)/ })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: /ElevenLabs: Eleven v3 \(OpenRouter\)/ })).toBeInTheDocument()
    })

    expect((imageSelect as HTMLSelectElement).value).toBe('automatic')
    expect((audioSelect as HTMLSelectElement).value).toBe('automatic')
    expect((videoSelect as HTMLSelectElement).value).toBe('automatic')

    // Filter image models using the search box and select Qwen Image 2.1 Pro
    const imageSearch = screen.getByLabelText('Filtrar modelos de imagem')
    await user.type(imageSearch, 'qwen 2.1')
    expect(screen.queryByRole('option', { name: /GPT Image 1/ })).toBeNull()

    await user.selectOptions(imageSelect, 'openrouter:qwen/qwen-image-2.1-pro')
    await waitFor(() => expect((imageSelect as HTMLSelectElement).value).toBe('openrouter:qwen/qwen-image-2.1-pro'))

    const imgPut = fetchImpl.mock.calls.find(
      ([reqInput, init]) => String(reqInput).endsWith('/v1/settings/media-models/image') && init?.method === 'PUT',
    )
    expect(imgPut).toBeDefined()
    expect(JSON.parse(String(imgPut?.[1]?.body))).toEqual({
      provider: 'openrouter',
      model_id: 'qwen/qwen-image-2.1-pro',
    })

    // Select Eleven v3 for audio
    await user.selectOptions(audioSelect, 'openrouter:elevenlabs/eleven-v3')
    await waitFor(() => expect((audioSelect as HTMLSelectElement).value).toBe('openrouter:elevenlabs/eleven-v3'))

    const audPut = fetchImpl.mock.calls.find(
      ([reqInput, init]) => String(reqInput).endsWith('/v1/settings/media-models/audio') && init?.method === 'PUT',
    )
    expect(audPut).toBeDefined()
    expect(JSON.parse(String(audPut?.[1]?.body))).toEqual({
      provider: 'openrouter',
      model_id: 'elevenlabs/eleven-v3',
    })
  })

  it('returns a modality back to automatic when Automático is selected', async () => {
    const fetchImpl = fetchStub({
      models: {
        openrouter: [
          mediaModel({
            provider: 'openrouter',
            model_id: 'qwen/qwen-image-2.1-pro',
            display_name: 'Qwen Image 2.1 Pro',
            output_modalities: ['image'],
          }),
        ],
      },
      settings: {
        image: { provider: 'openrouter', model_id: 'qwen/qwen-image-2.1-pro', mode: 'manual' },
        audio: { provider: null, model_id: null, mode: 'automatic' },
        video: { provider: null, model_id: null, mode: 'automatic' },
      },
    })
    const user = userEvent.setup()
    render(<MediaModelsSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const imageSelect = await screen.findByLabelText('Modelo padrão de imagem')
    await waitFor(() => expect((imageSelect as HTMLSelectElement).value).toBe('openrouter:qwen/qwen-image-2.1-pro'))

    await user.selectOptions(imageSelect, 'automatic')
    await waitFor(() => expect((imageSelect as HTMLSelectElement).value).toBe('automatic'))

    const putCall = fetchImpl.mock.calls.find(
      ([reqInput, init]) => String(reqInput).endsWith('/v1/settings/media-models/image') && init?.method === 'PUT',
    )
    expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({ provider: null, model_id: null })
  })
})
