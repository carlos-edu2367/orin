import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { VisionModelSetting } from '../../src/features/providers/VisionModelSetting'

afterEach(() => vi.restoreAllMocks())

function client(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function catalogModel(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    provider: 'openrouter', model_id: 'openrouter/vision-model', display_name: 'Vision Model', context_window: 1,
    capabilities: [], input_modalities: ['text', 'image'], output_modalities: ['text'], pricing: null,
    is_favorite: false, refreshed_at: null,
    ...overrides,
  }
}

function textOnlyModel(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    provider: 'anthropic', model_id: 'anthropic/text-model', display_name: 'Text Only Model', context_window: 1,
    capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null,
    is_favorite: false, refreshed_at: null,
    ...overrides,
  }
}

/** Every provider's `/models` route + the vision-model setting route, dispatched by URL. */
function fetchStub({
  models = {},
  setting = { provider: null, model_id: null, mode: 'automatic' },
  onPut,
}: {
  models?: Record<string, unknown[]>
  setting?: unknown
  onPut?: (body: unknown) => unknown
} = {}) {
  return vi.fn<typeof fetch>((input, init) => {
    const url = String(input)
    if (url.endsWith('/settings/vision-model') && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body))
      return Promise.resolve(json(onPut ? onPut(body) : { provider: body.provider, model_id: body.model_id, mode: body.provider === null ? 'automatic' : 'manual' }))
    }
    if (url.endsWith('/settings/vision-model')) {
      return Promise.resolve(json(setting))
    }
    const providerMatch = /\/v1\/providers\/([^/]+)\/models$/.exec(url)
    if (providerMatch) {
      return Promise.resolve(json({ items: models[providerMatch[1]] ?? [] }))
    }
    return Promise.resolve(json({}))
  })
}

describe('VisionModelSetting', () => {
  it('lists only the catalog models that accept image input, with Automático as the default selection', async () => {
    const fetchImpl = fetchStub({ models: { openrouter: [catalogModel()], anthropic: [textOnlyModel()] } })
    render(<VisionModelSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const select = await screen.findByLabelText('Modelo de leitura visual')
    await waitFor(() => expect(screen.getByRole('option', { name: /Vision Model/ })).toBeInTheDocument())

    expect(screen.queryByRole('option', { name: /Text Only Model/ })).toBeNull()
    expect((select as HTMLSelectElement).value).toBe('automatic')
    expect(screen.getByRole('option', { name: 'Automático' })).toBeInTheDocument()
  })

  it('calls the API with the chosen provider and model when a vision model is selected', async () => {
    const fetchImpl = fetchStub({ models: { openrouter: [catalogModel()] } })
    const user = userEvent.setup()
    render(<VisionModelSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const select = await screen.findByLabelText('Modelo de leitura visual')
    await waitFor(() => expect(screen.getByRole('option', { name: /Vision Model/ })).toBeInTheDocument())

    await user.selectOptions(select, 'openrouter:openrouter/vision-model')

    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('openrouter:openrouter/vision-model'))
    const putCall = fetchImpl.mock.calls.find(([reqInput, init]) => String(reqInput).endsWith('/settings/vision-model') && init?.method === 'PUT')
    expect(putCall).toBeDefined()
    expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({ provider: 'openrouter', model_id: 'openrouter/vision-model' })
  })

  it('shows the stored manual override as the initial selection', async () => {
    const fetchImpl = fetchStub({
      models: { openrouter: [catalogModel()] },
      setting: { provider: 'openrouter', model_id: 'openrouter/vision-model', mode: 'manual' },
    })
    render(<VisionModelSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const select = await screen.findByLabelText('Modelo de leitura visual')
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('openrouter:openrouter/vision-model'))
  })

  it('sends null/null to return to automatic', async () => {
    const fetchImpl = fetchStub({
      models: { openrouter: [catalogModel()] },
      setting: { provider: 'openrouter', model_id: 'openrouter/vision-model', mode: 'manual' },
    })
    const user = userEvent.setup()
    render(<VisionModelSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const select = await screen.findByLabelText('Modelo de leitura visual')
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('openrouter:openrouter/vision-model'))

    await user.selectOptions(select, 'automatic')

    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('automatic'))
    const putCall = fetchImpl.mock.calls.find(([reqInput, init]) => String(reqInput).endsWith('/settings/vision-model') && init?.method === 'PUT')
    expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({ provider: null, model_id: null })
  })

  it('explains that the file leaves the machine, which is why automatic prefers a local Ollama', async () => {
    const fetchImpl = fetchStub({ models: {} })
    render(<VisionModelSetting client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    expect(await screen.findByText(/enviado ao provider do modelo escolhido/)).toBeInTheDocument()
    expect(screen.getByText(/Ollama local/)).toBeInTheDocument()
  })
})
