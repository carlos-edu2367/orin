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
