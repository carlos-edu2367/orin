import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentsSection } from '../../src/features/codeSettings/AgentsSection'

type Call = { url: string; method: string; body: unknown }

function stubFetch(): Call[] {
  const calls: Call[] = []
  globalThis.fetch = vi.fn(async (input: Parameters<typeof fetch>[0], init?: NonNullable<Parameters<typeof fetch>[1]>) => {
    const call = { url: String(input), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(call)
    return new Response(JSON.stringify({ agents: [], errors: [], shadowed: [] }),{ status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return calls
}

const at = (path: string) => ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>

afterEach(() => { vi.restoreAllMocks() })

describe('W7-R6 "Novo agente" asks the server not to replace a file', () => {
  it('sends create: true for a new agent and not for an edit', async () => {
    const calls = stubFetch()
    render(<AgentsSection />, { wrapper: at('/settings/agents') })
    fireEvent.click(await screen.findByRole('button', { name: 'Novo agente' }))
    fireEvent.change(screen.getByLabelText(/^Nome/), { target: { value: 'revisor' } })
    fireEvent.change(screen.getByLabelText(/Quando usar/), { target: { value: 'Revisa testes' } })
    fireEvent.change(screen.getByLabelText(/Instruções/), { target: { value: 'Revise.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'PUT')?.body).toMatchObject({ scope: 'user', create: true }))
  })
})
