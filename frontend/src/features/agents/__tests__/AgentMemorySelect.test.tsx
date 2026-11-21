import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import { CustomAgentsSection } from '../CustomAgentsSection'

describe('CustomAgentsSection — Memória por agente (WS-D6)', () => {
  it('shows the Memória select with all four scopes and the inactive-flag note and submits selected value', async () => {
    const user = userEvent.setup()
    let submittedBody: Record<string, unknown> | null = null

    const request = vi.fn(
      async (opts: { path: string; method?: string; body?: unknown; parse: (v: unknown) => unknown }) => {
        if (opts.path === '/v1/custom-agents' && (!opts.method || opts.method === 'GET')) {
          return opts.parse({ agents: [] })
        }
        if (opts.path === '/v1/custom-agents' && opts.method === 'POST') {
          submittedBody = opts.body as Record<string, unknown>
          return opts.parse({
            agent: {
              name: 'revisor-sql',
              description: 'Revisa queries SQL',
              prompt: 'Revise consultas SQL.',
              provider: null,
              models: [],
              tools: null,
              disallowed_tools: [],
              effort: null,
              permission_mode: null,
              max_turns: null,
              memory: 'local',
              enabled: true,
              updated_at: '2026-11-21T12:00:00Z',
            },
          })
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
        <CustomAgentsSection client={client} />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Criar o primeiro agente/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Criar o primeiro agente/i }))

    const memorySelect = screen.getByLabelText('Memória') as HTMLSelectElement
    expect(memorySelect.value).toBe('project')
    expect(
      screen.getByText('Sem efeito enquanto a memória por agente estiver desligada'),
    ).toBeInTheDocument()

    await user.type(screen.getByLabelText('Nome'), 'revisor-sql')
    await user.type(screen.getByLabelText('Quando usar'), 'Revisa queries SQL')
    await user.type(screen.getByLabelText('Instruções'), 'Revise consultas SQL.')
    await user.selectOptions(memorySelect, 'local')

    await user.click(screen.getByRole('button', { name: 'Criar agente' }))

    await waitFor(() => {
      expect(submittedBody).not.toBeNull()
    })
    expect(submittedBody).toMatchObject({ name: 'revisor-sql', memory: 'local' })
  })
})
