import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { BackgroundSection } from '../../src/features/settings/BackgroundSection'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => { delete (window as { orinDesktop?: unknown }).orinDesktop })

describe('BackgroundSection', () => {
  it('turns on starting with the computer', async () => {
    const fetchImpl = vi.fn<typeof fetch>((_input, init) =>
      Promise.resolve(init?.method === 'PUT' ? json({ supported: true, enabled: true }) : json({ supported: true, enabled: false })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter><BackgroundSection client={client} /></MemoryRouter>)
    const box = await screen.findByRole('checkbox', { name: /Iniciar o Orin em segundo plano ao ligar o computador/ })
    expect(box).not.toBeChecked()
    await userEvent.click(box)

    expect(await screen.findByRole('checkbox', { name: /ao ligar o computador/ })).toBeChecked()
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true)
  })

  it('explains that autostart needs the installed Orin', async () => {
    const client = new ApiClient({ fetchImpl: vi.fn<typeof fetch>(() => Promise.resolve(json({ supported: false, enabled: false }))), maxAttempts: 1 })

    render(<MemoryRouter><BackgroundSection client={client} /></MemoryRouter>)

    expect(await screen.findByText(/disponível no Orin instalado/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('lets the desktop app choose what closing the window does', async () => {
    const setPreferences = vi.fn().mockResolvedValue({ closeBehavior: 'background' })
    ;(window as { orinDesktop?: unknown }).orinDesktop = { onUpdateAvailable: () => () => undefined, getPreferences: () => Promise.resolve({ closeBehavior: 'ask' }), setPreferences }
    const client = new ApiClient({ fetchImpl: vi.fn<typeof fetch>(() => Promise.resolve(json({ supported: false, enabled: false }))), maxAttempts: 1 })

    render(<MemoryRouter><BackgroundSection client={client} /></MemoryRouter>)
    expect(await screen.findByRole('radio', { name: /Perguntar/ })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: /Manter em segundo plano/ }))

    expect(setPreferences).toHaveBeenCalledWith({ closeBehavior: 'background' })
    expect(screen.getByRole('radio', { name: /Manter em segundo plano/ })).toBeChecked()
  })
})
