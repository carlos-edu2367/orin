import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ProviderSettingsPage } from '../../src/features/providers/ProviderSettingsPage'
import { PROVIDER_NAMES, testOllamaConnection } from '../../src/api/providers'

const apiKey = 'sk-openrouter-test-key'

afterEach(() => vi.restoreAllMocks())

describe('ProviderSettingsPage OpenRouter setup', () => {
  it('sends api_key and enabled, then clears the key after an accepted save', async () => {
    const fetchImpl = providerFetch(() => json({ provider: 'openrouter', enabled: true }))
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await openRouterPanel()
    await user.type(within(panel).getByLabelText('Chave de API'), apiKey)
    await user.click(within(panel).getByRole('button', { name: 'Salvar' }))

    expect(await within(panel).findByRole('status')).toHaveTextContent('Habilitado')
    expect(within(panel).getByLabelText('Chave de API')).toHaveValue('')
    const request = putRequest(fetchImpl)
    expect(JSON.parse(String(request[1]?.body))).toEqual({ api_key: apiKey, enabled: true })
    expect(new Headers(request[1]?.headers).get('X-CSRF-Token')).toBe('csrf-test')
    expect(document.body.textContent).not.toContain(apiKey)
  })

  it('keeps the key and explains when authentication has expired', async () => {
    const fetchImpl = providerFetch(() => error('authentication_required', 'AUTHENTICATION', 'corr-auth', 401))
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await openRouterPanel()
    await user.type(within(panel).getByLabelText('Chave de API'), apiKey)
    await user.click(within(panel).getByRole('button', { name: 'Salvar' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent('Sua sessão expirou. Entre novamente para salvar a chave.')
    expect(within(panel).getByLabelText('Chave de API')).toHaveValue(apiKey)
    expect(document.body.textContent).not.toContain(apiKey)
  })

  it('keeps the key and requests a refresh when CSRF validation is rejected', async () => {
    const fetchImpl = providerFetch(() => error('access_denied', 'AUTHORIZATION', 'corr-csrf', 403))
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await openRouterPanel()
    await user.type(within(panel).getByLabelText('Chave de API'), apiKey)
    await user.click(within(panel).getByRole('button', { name: 'Salvar' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent('Atualize a página para renovar sua sessão.')
    expect(within(panel).getByLabelText('Chave de API')).toHaveValue(apiKey)
  })

  it('shows a provider failure and correlation ID without rendering the key', async () => {
    const fetchImpl = providerFetch(() => error('provider_unavailable', 'INTERNAL', 'corr-provider-503', 503))
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await openRouterPanel()
    await user.type(within(panel).getByLabelText('Chave de API'), apiKey)
    await user.click(within(panel).getByRole('button', { name: 'Salvar' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent('O provider não está disponível no momento.')
    expect(within(panel).getByRole('alert')).toHaveTextContent('Correlation ID: corr-provider-503')
    expect(document.body.textContent).not.toContain(apiKey)
  })

  it('disables saving without CSRF and does not issue a PUT', async () => {
    const fetchImpl = providerFetch(() => json({ provider: 'openrouter', enabled: true }))
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'missing_csrf' }} />)

    const panel = await openRouterPanel()
    await user.type(within(panel).getByLabelText('Chave de API'), apiKey)

    expect(within(panel).getByText('Não foi possível confirmar sua sessão segura. Atualize a página antes de salvar.')).toHaveAttribute('role', 'status')
    expect(within(panel).getByRole('button', { name: 'Salvar' })).toBeDisabled()
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('disables revoke, catalog refresh, and model favorites without CSRF and issues no provider mutation', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'GET' && url.endsWith('/models')) {
        return Promise.resolve(json({ items: [model()] }))
      }
      return Promise.resolve(json({ provider: 'openrouter', enabled: true }))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'missing_csrf' }} />)

    const panel = await openRouterPanel()
    await within(panel).findByRole('button', { name: 'Revogar acesso' })
    await user.click(within(panel).getByRole('button', { name: 'Ver modelos autorizados' }))

    expect(within(panel).getByRole('button', { name: 'Revogar acesso' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: 'Atualizar catálogo' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: 'Favoritar' })).toBeDisabled()
    await user.click(within(panel).getByRole('button', { name: 'Revogar acesso' }))
    await user.click(within(panel).getByRole('button', { name: 'Atualizar catálogo' }))
    await user.click(within(panel).getByRole('button', { name: 'Favoritar' }))
    expect(fetchImpl.mock.calls.filter(([, init]) => init?.method !== 'GET')).toHaveLength(0)
  })

  it('shows an unconfigured provider as available when its initial inspection returns 404', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(error('resource_not_found', 'NOT_FOUND', 'corr-not-configured', 404)))
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'loopback' }} />)

    const panel = await openRouterPanel()
    expect(within(panel).getByRole('status')).toHaveTextContent('Não configurado')
    expect(within(panel).getByRole('button', { name: 'Salvar' })).toBeDisabled()
  })

  it('keeps OmniRoute optional, tests its public gateway, and saves the selected base URL', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'GET') return Promise.resolve(error('resource_not_found', 'NOT_FOUND', 'corr-empty', 404))
      if (url.endsWith('/omniroute/install')) return Promise.resolve(json({ installed: true, next_step: 'omniroute' }))
      if (url.endsWith('/omniroute/test')) return Promise.resolve(json({ connected: true, models_available: 3, base_url: 'http://localhost:20128/v1' }))
      if (init?.method === 'PUT') return Promise.resolve(json({ provider: 'omniroute', enabled: true, base_url: 'http://localhost:20128/v1' }))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await screen.findByRole('article', { name: 'OmniRoute' })
    expect(within(panel).getByText('Instale o gateway local uma vez, conecte sua conta e mantenha modelos, fallback e roteamento em um único lugar.')).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Configurar OmniRoute' }))
    await user.click(within(panel).getByRole('button', { name: 'Instalar OmniRoute' }))
    expect(await within(panel).findByText('Instalação concluída. Execute', { exact: false })).toBeInTheDocument()
    await user.type(within(panel).getByLabelText(/Chave de API/), 'omni-key')
    await user.click(within(panel).getByRole('button', { name: 'Testar conexão' }))
    expect(await within(panel).findByRole('status')).toHaveTextContent('Conectado')
    await user.click(within(panel).getByRole('button', { name: 'Salvar e ativar' }))

    const request = putRequest(fetchImpl)
    expect(JSON.parse(String(request[1]?.body))).toEqual({ api_key: 'omni-key', enabled: true, base_url: 'http://localhost:20128/v1' })
    expect(document.body.textContent).not.toContain('omni-key')
  })

  it('exposes a catalog refresh control for OmniRoute, since it has no standard form to host one', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/models:refresh')) return Promise.resolve(json({ count: 1, refreshed_at: '2026-08-12T00:00:00Z' }))
      if (init?.method === 'GET' && url.endsWith('/models')) return Promise.resolve(json({ items: [{ ...model(), provider: 'omniroute', model_id: 'auto/coding' }] }))
      if (init?.method === 'GET') return Promise.resolve(json({ provider: 'omniroute', enabled: true, base_url: 'http://localhost:20128/v1' }))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await screen.findByRole('article', { name: 'OmniRoute' })
    const refreshButton = await within(panel).findByRole('button', { name: 'Atualizar catálogo' })
    await user.click(refreshButton)

    expect(fetchImpl.mock.calls.some(([requestInput]) => String(requestInput).endsWith('/models:refresh'))).toBe(true)
    expect(await within(panel).findByText('auto/coding')).toBeInTheDocument()
  })

  it('explains an empty catalog instead of silently rendering nothing', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'GET' && url.endsWith('/models')) return Promise.resolve(json({ items: [] }))
      if (init?.method === 'GET') return Promise.resolve(json({ provider: 'openrouter', enabled: true }))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await openRouterPanel()
    await user.click(within(panel).getByRole('button', { name: 'Ver modelos autorizados' }))

    expect(await within(panel).findByText('Nenhum modelo no catálogo. Use "Atualizar catálogo" para buscá-los do provider.')).toBeInTheDocument()
  })

  it('surfaces a catalog listing failure instead of swallowing it', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'GET' && url.endsWith('/models')) return Promise.resolve(error('provider_unavailable', 'INTERNAL', 'corr-catalog-500', 500))
      if (init?.method === 'GET') return Promise.resolve(json({ provider: 'openrouter', enabled: true }))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await openRouterPanel()
    await user.click(within(panel).getByRole('button', { name: 'Ver modelos autorizados' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent('O provider não está disponível no momento.')
  })

  it('populates the catalog when the OmniRoute flow is saved, so the composer stops asking for setup', async () => {
    const calls: string[] = []
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.endsWith('/models:refresh')) return Promise.resolve(json({ count: 1, refreshed_at: '2026-08-12T00:00:00Z' }))
      if (init?.method === 'GET' && url.endsWith('/models')) {
        const listed = calls.some((entry) => entry.includes('/models:refresh'))
        return Promise.resolve(json({ items: listed ? [{ ...model(), provider: 'omniroute', model_id: 'auto/coding' }] : [] }))
      }
      if (init?.method === 'GET') return Promise.resolve(error('resource_not_found', 'NOT_FOUND', 'corr-empty', 404))
      if (init?.method === 'PUT') return Promise.resolve(json({ provider: 'omniroute', enabled: true, base_url: 'http://localhost:20128/v1' }))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await screen.findByRole('article', { name: 'OmniRoute' })
    await user.click(within(panel).getByRole('button', { name: 'Configurar OmniRoute' }))
    await user.click(within(panel).getByRole('button', { name: 'Salvar e ativar' }))

    expect(await within(panel).findByText('auto/coding')).toBeInTheDocument()
    expect(calls.some((entry) => entry.startsWith('POST') && entry.endsWith('/models:refresh'))).toBe(true)
  })

  it('recognizes an OmniRoute installation that completed outside the current request', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'GET' && url.endsWith('/omniroute/install')) return Promise.resolve(json({ installed: true }))
      if (init?.method === 'GET') return Promise.resolve(error('resource_not_found', 'NOT_FOUND', 'corr-empty', 404))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await screen.findByRole('article', { name: 'OmniRoute' })
    await user.click(within(panel).getByRole('button', { name: 'Configurar OmniRoute' }))

    expect(await within(panel).findByRole('button', { name: 'OmniRoute instalado' })).toBeDisabled()
  })

  it('explains that the local API must be restarted when its installer route is unavailable', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'GET') return Promise.resolve(error('resource_not_found', 'NOT_FOUND', 'corr-empty', 404))
      if (url.endsWith('/omniroute/install')) return Promise.resolve(new Response(JSON.stringify({ detail: 'Method Not Allowed' }), { status: 405, headers: { 'Content-Type': 'application/json' } }))
      return Promise.resolve(json({}))
    })
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(fetchImpl)} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await screen.findByRole('article', { name: 'OmniRoute' })
    await user.click(within(panel).getByRole('button', { name: 'Configurar OmniRoute' }))
    await user.click(within(panel).getByRole('button', { name: 'Instalar OmniRoute' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent('A API do Orin precisa ser reiniciada para disponibilizar o instalador do OmniRoute.')
  })
})

function client(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
}

function providerFetch(putResponse: () => Response) {
  return vi.fn<typeof fetch>((_input, init) => {
    if (init?.method === 'PUT') return Promise.resolve(putResponse())
    return Promise.resolve(json({}))
  })
}

function putRequest(fetchImpl: ReturnType<typeof providerFetch>) {
  const request = fetchImpl.mock.calls.find(([, init]) => init?.method === 'PUT')
  if (!request) throw new Error('Expected a PUT request')
  return request
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function error(code: string, category: string, correlationId: string, status: number): Response {
  return new Response(JSON.stringify({ error: { code, category, message_key: code, correlation_id: correlationId, retryable: false, retry_after: null } }), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function model() {
  return {
    provider: 'openrouter', model_id: 'openrouter/test-model', display_name: 'Test model', context_window: 1,
    capabilities: [], input_modalities: [], output_modalities: [], pricing: null, is_favorite: false, refreshed_at: null,
  }
}

async function openRouterPanel(): Promise<HTMLElement> {
  return screen.findByRole('article', { name: 'OpenRouter' })
}

describe('ollama provider client', () => {
  it('is offered alongside the other providers', () => {
    expect(PROVIDER_NAMES).toContain('ollama')
  })

  it('posts the base url and key to the ollama test route', async () => {
    const seen: Array<Record<string, unknown>> = []
    const client = {
      createMutationIntent: () => ({ key: 'intent-1' }),
      request: async (options: Record<string, unknown>) => {
        seen.push(options)
        return (options.parse as (value: unknown) => unknown)({ connected: true, models_available: 3, base_url: 'http://localhost:11434' })
      },
    } as never

    const result = await testOllamaConnection(client, { apiKey: '', baseUrl: 'http://localhost:11434' })

    expect(seen[0].path).toBe('/v1/providers/ollama/test')
    expect(seen[0].method).toBe('POST')
    expect(seen[0].body).toEqual({ api_key: '', base_url: 'http://localhost:11434' })
    expect(result).toEqual({ connected: true, models_available: 3, base_url: 'http://localhost:11434' })
  })
})
