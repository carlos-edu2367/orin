import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { AboutSection } from '../../src/features/settings/AboutSection'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function status(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    installation_kind: 'installed', current_version: '0.2.4',
    installed_versions: [{ version: '0.2.4', is_current: true, removable: false }],
    latest_release: { version: '0.2.5', url: 'https://example.test/releases/v0.2.5' },
    latest_release_error: null, update_available: true, checked_at: '2026-08-18T00:00:00Z',
    ...overrides,
  }
}

describe('AboutSection', () => {
  it('shows an install button when a newer release is available', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json(status())))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)

    expect(await screen.findByRole('button', { name: 'Baixar v0.2.5' })).toBeInTheDocument()
  })

  it('hides the install button when already on the latest version', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json(status({ update_available: false, current_version: '0.2.5' }))))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)

    await screen.findByText('Versões instaladas')
    expect(screen.queryByRole('button', { name: /Instalar/ })).not.toBeInTheDocument()
  })

  it('hides the install button on a development checkout even if the version string compares lower', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json(status({ installation_kind: 'development' }))))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)

    await screen.findByText('Versões instaladas')
    expect(screen.queryByRole('button', { name: /Instalar/ })).not.toBeInTheDocument()
  })

  it('downloads in the background and offers the terminal command once ready', async () => {
    let prepared = false
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'POST' && url.endsWith('/v1/installation/update/prepare')) { prepared = true; return Promise.resolve(json({ state: 'ready', current_version: '0.2.4', version: '0.2.5' }, 202)) }
      if (url.endsWith('/v1/installation/update/status')) return Promise.resolve(json({ state: prepared ? 'ready' : 'idle', current_version: '0.2.4', version: prepared ? '0.2.5' : null }))
      return Promise.resolve(json(status()))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
    const user = userEvent.setup()

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)
    await user.click(await screen.findByRole('button', { name: 'Baixar v0.2.5' }))

    expect(await screen.findByText(/baixada e verificada/)).toBeInTheDocument()
    expect(screen.getByText('orin update')).toBeInTheDocument()
  })

  it('shows the reason and a retry when the download fails', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (init?.method === 'POST') return Promise.resolve(json({ state: 'failed', current_version: '0.2.4', error: { message: 'Sem conexão com o servidor de releases.', hint: 'Verifique sua internet.' } }, 202))
      if (url.endsWith('/v1/installation/update/status')) return Promise.resolve(json({ state: 'idle', current_version: '0.2.4' }))
      return Promise.resolve(json(status()))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
    const user = userEvent.setup()

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)
    await user.click(await screen.findByRole('button', { name: 'Baixar v0.2.5' }))

    expect(await screen.findByText(/Sem conexão com o servidor de releases/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument()
  })
})
