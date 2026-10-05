import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UpdateBanner } from '../../src/components/UpdateBanner'

type Candidate = { currentVersion: string; latestVersion: string }

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('UpdateBanner', () => {
  let listener: ((update: Candidate) => void) | undefined
  const applyUpdate = vi.fn<() => Promise<boolean>>()
  let jobState: Record<string, unknown>
  let calls: Array<{ url: string; method: string }>

  beforeEach(() => {
    listener = undefined
    calls = []
    jobState = { state: 'idle', current_version: '0.1.10' }
    applyUpdate.mockReset()
    applyUpdate.mockResolvedValue(true)
    window.localStorage.clear()
    window.orinDesktop = {
      onUpdateAvailable: (callback) => { listener = callback; return () => { listener = undefined } },
      applyUpdate,
    }
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      if (method === 'POST' && url.endsWith('/update/prepare')) {
        jobState = { state: 'ready', current_version: '0.1.10', version: '0.1.11', notes: 'Mais rápido' }
        return Promise.resolve(json(jobState, 202))
      }
      return Promise.resolve(json(jobState))
    }))
  })

  afterEach(() => {
    delete window.orinDesktop
    vi.unstubAllGlobals()
  })

  it('announces the release, downloads in the background, then restarts onto it', async () => {
    render(<UpdateBanner />)
    act(() => listener?.({ currentVersion: '0.1.10', latestVersion: '0.1.11' }))

    expect(await screen.findByText('Versão atual 0.1.10 - Versão mais recente 0.1.11')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Baixar atualização' }))

    expect(await screen.findByText('Versão 0.1.11 baixada e verificada')).toBeInTheDocument()
    expect(screen.getByText('Mais rápido')).toBeInTheDocument()
    expect(applyUpdate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Reiniciar para atualizar' }))

    await waitFor(() => expect(applyUpdate).toHaveBeenCalledOnce())
    expect(await screen.findByText('Instalando a versão 0.1.11')).toBeInTheDocument()
  })

  it('shows live progress while the backend downloads', async () => {
    jobState = { state: 'downloading', current_version: '0.1.10', version: '0.1.11', label: 'Baixando a versão 0.1.11', progress: 0.42, bytes_done: 63_000_000, bytes_total: 150_000_000 }
    render(<UpdateBanner />)

    expect(await screen.findByText('Baixando a versão 0.1.11 — 42%')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Progresso do download da atualização' })).toBeInTheDocument()
    expect(screen.getByText(/Você pode continuar usando o Orin/)).toBeInTheDocument()
  })

  it('offers a retry with the reason when the download fails', async () => {
    jobState = { state: 'failed', current_version: '0.1.10', error: { message: 'O arquivo baixado não confere com a assinatura publicada e foi descartado.', hint: 'Nada foi instalado.' } }
    render(<UpdateBanner />)

    expect(await screen.findByText(/não confere com a assinatura/)).toBeInTheDocument()
    expect(screen.getByText('Nada foi instalado.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument()
  })

  it('tells the person once when an update undid itself, and remembers it was read', async () => {
    jobState = { state: 'idle', current_version: '0.1.10', last_attempt: { status: 'rolled_back', version: '0.1.11', restored: '0.1.10', message: 'a nova versão não iniciou', at: '2026-10-05T10:00:00+0000' } }
    const { unmount } = render(<UpdateBanner />)

    expect(await screen.findByText(/A versão 0.1.11 não funcionou e o Orin voltou para a 0.1.10/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Entendi' }))
    await waitFor(() => expect(screen.queryByText(/não funcionou/)).not.toBeInTheDocument())

    unmount()
    render(<UpdateBanner />)
    await waitFor(() => expect(calls.length).toBeGreaterThan(1))
    expect(screen.queryByText(/não funcionou/)).not.toBeInTheDocument()
  })

  it('welcomes the new version after a successful update', async () => {
    jobState = { state: 'idle', current_version: '0.1.11', last_attempt: { status: 'updated', version: '0.1.11', at: '2026-10-05T10:00:00+0000' } }
    render(<UpdateBanner />)

    expect(await screen.findByText('Agora você está na versão 0.1.11.')).toBeInTheDocument()
  })

  it('stays out of the way outside the desktop app', () => {
    delete window.orinDesktop
    const { container } = render(<UpdateBanner />)

    expect(container).toBeEmptyDOMElement()
    expect(calls).toEqual([])
  })
})
