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

    expect(await screen.findByRole('button', { name: 'Instalar v0.2.5' })).toBeInTheDocument()
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

  it('starts the install and reports success', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      if (init?.method === 'POST') return Promise.resolve(json({ started: true }))
      return Promise.resolve(json(status()))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
    const user = userEvent.setup()

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)
    const button = await screen.findByRole('button', { name: 'Instalar v0.2.5' })
    await user.click(button)

    expect(await screen.findByText(/Nova versão instalada/)).toBeInTheDocument()
    expect(fetchImpl.mock.calls.some(([requestInput]) => String(requestInput).endsWith('/v1/installation/update'))).toBe(true)
  })

  it('reports a failed install without crashing', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      if (init?.method === 'POST') return Promise.resolve(json({ error: 'boom' }, 500))
      return Promise.resolve(json(status()))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
    const user = userEvent.setup()

    render(<MemoryRouter><AboutSection client={client} /></MemoryRouter>)
    const button = await screen.findByRole('button', { name: 'Instalar v0.2.5' })
    await user.click(button)

    expect(await screen.findByText('Não foi possível instalar a nova versão.')).toBeInTheDocument()
  })
})
