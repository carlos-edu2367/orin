import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import { AgentsSection } from '../AgentsSection'

describe('AgentsSection — Modo de acesso (Frente B)', () => {
  it('selects access_mode, disables incompatible fields in prompt_only, and submits access_mode to settings API', async () => {
    const user = userEvent.setup()
    let submittedBody: Record<string, unknown> | null = null

    const request = vi.fn(
      async (opts: { path: string; method?: string; body?: unknown; parse: (v: unknown) => unknown }) => {
        if (opts.path === '/v1/settings/agents' && (!opts.method || opts.method === 'GET')) {
          return opts.parse({ agents: [], errors: [], shadowed: [] })
        }
        if (opts.path.startsWith('/v1/settings/agents/') && opts.method === 'PUT') {
          submittedBody = opts.body as Record<string, unknown>
          return opts.parse({ agents: [], errors: [], shadowed: [] })
        }
        if (opts.path.includes('/models')) {
          return opts.parse({ models: [] })
        }
        return opts.parse({})
      },
    )

    const client = { request } as unknown as ApiClient

    render(
      <MemoryRouter>
        <AgentsSection client={client} />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Novo agente/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('button', { name: /Novo agente/i }))

    const accessSelect = screen.getByLabelText('Modo de acesso') as HTMLSelectElement
    expect(accessSelect.value).toBe('full')
    expect(Array.from(accessSelect.options).map((o) => o.value)).toEqual(['full', 'read_only', 'prompt_only'])

    await user.selectOptions(accessSelect, 'prompt_only')
    expect((screen.getByLabelText('Isolamento') as HTMLSelectElement).disabled).toBe(true)
    expect((screen.getByPlaceholderText('Read, Grep, Glob') as HTMLInputElement).disabled).toBe(true)

    await user.type(screen.getByLabelText(/Nome/i), 'juiz-file')
    await user.type(screen.getByLabelText(/Quando usar/i), 'Valida screenshots')
    await user.type(screen.getByLabelText(/Instruções/i), 'Compare as imagens anexadas.')

    await user.click(screen.getByRole('button', { name: 'Salvar' }))

    await waitFor(() => {
      expect(submittedBody).not.toBeNull()
    })
    expect(submittedBody).toMatchObject({
      description: 'Valida screenshots',
      access_mode: 'prompt_only',
      tools: null,
      disallowed_tools: [],
    })
  })
})
