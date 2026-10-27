import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ProviderDetail } from '../../src/features/providers/ProviderDetail'

describe('ProviderDetail', () => {
  it('keeps the credential field write-only and renders a provider-specific setup', () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify({ provider: 'openai', enabled: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    render(<MemoryRouter><ProviderDetail provider="openai" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('region', { name: 'OpenAI' })).toBeInTheDocument()
    expect(screen.getByLabelText('Chave de API')).toHaveValue('')
  })

  it('exposes a catalog refresh control for Ollama, since it has no standard form to host one', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/models:refresh')) return Promise.resolve(json({ count: 1, refreshed_at: '2026-08-12T00:00:00Z' }))
      if (init?.method === 'GET' && url.endsWith('/models')) return Promise.resolve(json({ items: [{ ...model(), provider: 'ollama', model_id: 'cloud-only-model' }] }))
      if (init?.method === 'GET') return Promise.resolve(json({ provider: 'ollama', enabled: true, base_url: 'https://ollama.com' }))
      return Promise.resolve(json({}))
    })
    const client = new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
    const user = userEvent.setup()
    render(<MemoryRouter><ProviderDetail provider="ollama" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} onClose={() => {}} /></MemoryRouter>)

    const region = await screen.findByRole('region', { name: 'Ollama' })
    const refreshButton = await within(region).findByRole('button', { name: 'Atualizar catálogo' })
    await user.click(refreshButton)

    expect(fetchImpl.mock.calls.some(([requestInput]) => String(requestInput).endsWith('/models:refresh'))).toBe(true)
    expect(await within(region).findByText('cloud-only-model')).toBeInTheDocument()
  })

  it('registers a model manually and reloads it into the authorized catalog', async () => {
    let added = false
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/models') && init?.method === 'POST') {
        added = true
        return Promise.resolve(json({ ...model(), provider: 'openai', model_id: 'deepseek-v4-flash', display_name: 'deepseek-v4-flash', is_custom: true }))
      }
      if (url.endsWith('/models') && init?.method === 'GET') {
        return Promise.resolve(json({ items: added ? [{ ...model(), provider: 'openai', model_id: 'deepseek-v4-flash', display_name: 'deepseek-v4-flash', is_custom: true }] : [] }))
      }
      if (init?.method === 'GET') return Promise.resolve(json({ provider: 'openai', enabled: true }))
      return Promise.resolve(json({}))
    })
    const client = new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
    const user = userEvent.setup()
    render(<MemoryRouter><ProviderDetail provider="openai" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} onClose={() => {}} /></MemoryRouter>)

    const region = await screen.findByRole('region', { name: 'OpenAI' })
    await user.type(within(region).getByLabelText('Adicionar modelo manualmente'), 'deepseek-v4-flash')
    await user.click(within(region).getByRole('button', { name: 'Adicionar modelo' }))

    expect((await within(region).findAllByText('deepseek-v4-flash')).length).toBeGreaterThan(0)
    const request = fetchImpl.mock.calls.find(([input, init]) => String(input).endsWith('/models') && init?.method === 'POST')
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ model_id: 'deepseek-v4-flash' })
  })

  it('renders the key fallback list under the provider form', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input) => {
      const url = String(input)
      if (url.endsWith('/keys')) return Promise.resolve(json([{ id: 1, label: 'conta free 1', position: 0, status: 'active', cooldown_until: null }]))
      return Promise.resolve(json({ provider: 'openai', enabled: true, key_cooldown_seconds: 90 }))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    render(<MemoryRouter><ProviderDetail provider="openai" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)

    expect(await screen.findByText('conta free 1')).toBeInTheDocument()
    expect(screen.getByText('Principal')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('Tempo de cooldown (s)')).toHaveValue(90))
  })

  it('supports OpenCode and refreshes the fallback key list after saving the primary key', async () => {
    let savedPrimary = false
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/v1/providers/opencode/keys') && (!init?.method || init.method === 'GET')) {
        return Promise.resolve(json(savedPrimary ? [{ id: 1, label: null, position: 0, status: 'active', cooldown_until: null }] : []))
      }
      if (url.endsWith('/v1/providers/opencode') && init?.method === 'PUT') {
        savedPrimary = true
        return Promise.resolve(json({ provider: 'opencode', enabled: true, key_cooldown_seconds: 60 }))
      }
      return Promise.resolve(json({ provider: 'opencode', enabled: savedPrimary, key_cooldown_seconds: 60 }))
    })
    const client = new ApiClient({ fetchImpl, csrfToken: 'csrf', maxAttempts: 1, createIdempotencyKey: () => 'intent-opencode' })
    const user = userEvent.setup()
    render(<MemoryRouter><ProviderDetail provider="opencode" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)

    const region = await screen.findByRole('region', { name: 'OpenCode' })
    await user.type(within(region).getByLabelText('Chave de API'), 'oc-primary-key')
    await user.click(within(region).getByRole('button', { name: 'Salvar' }))

    expect(await within(region).findByText('Chave 1')).toBeInTheDocument()
    expect(within(region).getByText('Principal')).toBeInTheDocument()
  })

  it('supports OpenCode Local Bridge mode without requiring an API key and tests connection', async () => {
    let saved = false
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/v1/providers/opencode/test') && init?.method === 'POST') {
        return Promise.resolve(json({ connected: true, models_available: 10, base_url: 'http://127.0.0.1:18446' }))
      }
      if (url.endsWith('/v1/providers/opencode') && init?.method === 'PUT') {
        saved = true
        return Promise.resolve(json({ provider: 'opencode', enabled: true, base_url: 'http://127.0.0.1:18446', key_cooldown_seconds: 60 }))
      }
      if (url.endsWith('/v1/providers/opencode/models:refresh') && init?.method === 'POST') return Promise.resolve(json({ count: 1, refreshed_at: '2026-10-20T15:20:00Z' }))
      if (url.endsWith('/v1/providers/opencode/models') && (!init?.method || init.method === 'GET')) return Promise.resolve(json({ items: [{ ...model(), provider: 'opencode', model_id: 'deepseek-v4.1-flash-free', display_name: 'DeepSeek V4.1 Flash Free' }] }))
      if (url.endsWith('/v1/providers/opencode/keys')) {
        return Promise.resolve(json([]))
      }
      return Promise.resolve(json({ provider: 'opencode', enabled: saved ? true : null, base_url: 'http://127.0.0.1:18446', key_cooldown_seconds: 60 }))
    })
    const client = new ApiClient({ fetchImpl, csrfToken: 'csrf', maxAttempts: 1, createIdempotencyKey: () => 'intent-opencode-bridge' })
    const user = userEvent.setup()
    render(<MemoryRouter><ProviderDetail provider="opencode" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)

    const region = await screen.findByRole('region', { name: 'OpenCode' })
    expect(within(region).getByRole('radio', { name: 'Bridge Local (Grátis)' })).toHaveAttribute('aria-checked', 'true')
    expect(within(region).getByLabelText('URL do servidor')).toHaveValue('http://127.0.0.1:18446')

    await user.click(within(region).getByRole('button', { name: 'Testar conexão' }))
    expect(await within(region).findByText(/10 modelos disponíveis/)).toBeInTheDocument()

    const saveButton = within(region).getByRole('button', { name: 'Salvar' })
    expect(saveButton).not.toBeDisabled()
    await user.click(saveButton)
    expect(await within(region).findByText('DeepSeek V4.1 Flash Free')).toBeInTheDocument()
    expect(fetchImpl.mock.calls.some(([requestInput]) => String(requestInput).endsWith('/v1/providers/opencode/models:refresh'))).toBe(true)

    const putCall = fetchImpl.mock.calls.find(([input, init]) => String(input).endsWith('/v1/providers/opencode') && init?.method === 'PUT')
    expect(putCall).toBeDefined()
    expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({
      api_key: '',
      enabled: true,
      base_url: 'http://127.0.0.1:18446',
    })
  })

  it('supports NVIDIA NIM setup, tests connection, and reactivates an exhausted key from the fallback list', async () => {
    let keyStatus: 'exhausted' | 'active' = 'exhausted'
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/v1/providers/nvidia/test') && init?.method === 'POST') {
        return Promise.resolve(json({ reachable: true, authenticated: true, model_count: 24 }))
      }
      if (url.endsWith('/v1/providers/nvidia/keys/1:activate') && init?.method === 'POST') {
        keyStatus = 'active'
        return Promise.resolve(json({ id: 1, label: 'NVIDIA Trial 1', position: 0, status: 'active', cooldown_until: null }))
      }
      if (url.endsWith('/v1/providers/nvidia/keys')) {
        return Promise.resolve(json([{ id: 1, label: 'NVIDIA Trial 1', position: 0, status: keyStatus, cooldown_until: null }]))
      }
      return Promise.resolve(json({ provider: 'nvidia', enabled: true, base_url: 'https://integrate.api.nvidia.com/v1', key_cooldown_seconds: 60 }))
    })
    const client = new ApiClient({ fetchImpl, csrfToken: 'csrf', maxAttempts: 1, createIdempotencyKey: () => 'intent-nvidia' })
    const user = userEvent.setup()
    render(<MemoryRouter><ProviderDetail provider="nvidia" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)

    const region = await screen.findByRole('region', { name: 'NVIDIA NIM' })
    expect(within(region).getByRole('radio', { name: 'NVIDIA NIM Cloud' })).toHaveAttribute('aria-checked', 'true')
    expect(within(region).getByLabelText('URL do servidor')).toHaveValue('https://integrate.api.nvidia.com/v1')

    await user.click(within(region).getByRole('button', { name: 'Testar conexão' }))
    expect(await within(region).findByText(/24 modelos disponíveis/)).toBeInTheDocument()

    expect(await within(region).findByText('Sem saldo / Esgotada')).toBeInTheDocument()
    await user.click(within(region).getByRole('button', { name: 'Reativar' }))
    expect(await within(region).findByText('Ativa')).toBeInTheDocument()

    await user.click(within(region).getByRole('radio', { name: 'Self-Hosted NIM' }))
    expect(within(region).queryByText(/24 modelos disponíveis/)).toBeNull()
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function model() {
  return {
    provider: 'ollama', model_id: 'test-model', display_name: 'Test model', context_window: 1,
    capabilities: [], input_modalities: [], output_modalities: [], pricing: null, is_favorite: false, refreshed_at: null,
  }
}
