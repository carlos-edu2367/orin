import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { BrowserSection } from '../../src/features/settings/BrowserSection'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
const engine = (state: string, extra: Record<string, unknown> = {}) => ({ state, installed: state === 'ready', progress: null, message: null, error: null, install_command: 'orin browser install', ...extra })

describe('BrowserSection', () => {
  it('offers the install when Chromium is missing and starts it on click', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) =>
      Promise.resolve(init?.method === 'POST' ? json(engine('installing', { progress: 0, message: 'Preparando o download' }), 202) : json(engine('missing'))))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter><BrowserSection client={client} /></MemoryRouter>)
    await userEvent.click(await screen.findByRole('button', { name: 'Instalar navegador' }))

    expect(await screen.findByRole('progressbar', { name: 'Progresso da instalação do navegador' })).toBeInTheDocument()
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true)
  })

  it('shows no install button once the browser is ready', async () => {
    const client = new ApiClient({ fetchImpl: vi.fn<typeof fetch>(() => Promise.resolve(json(engine('ready')))), maxAttempts: 1 })

    render(<MemoryRouter><BrowserSection client={client} /></MemoryRouter>)

    expect(await screen.findByText(/Instalado\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Instalar|Tentar/ })).not.toBeInTheDocument()
  })

  it('explains a failure and offers a retry plus the terminal command', async () => {
    const client = new ApiClient({ fetchImpl: vi.fn<typeof fetch>(() => Promise.resolve(json(engine('failed', { error: 'Sem conexão.' })))), maxAttempts: 1 })

    render(<MemoryRouter><BrowserSection client={client} /></MemoryRouter>)

    expect(await screen.findByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument()
    expect(screen.getByText(/Sem conexão\./)).toBeInTheDocument()
    expect(screen.getByText('orin browser install')).toBeInTheDocument()
  })
})
