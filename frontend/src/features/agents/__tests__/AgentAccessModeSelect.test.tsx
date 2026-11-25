import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import { AGENT_TEMPLATES, CustomAgentsSection } from '../CustomAgentsSection'

describe('CustomAgentsSection — Modo de acesso (Frente B)', () => {
  it('includes the Validador visual (juiz) template preconfigured as prompt_only', () => {
    const tpl = AGENT_TEMPLATES.find((item) => item.id === 'validador-visual')
    expect(tpl).toBeDefined()
    expect(tpl?.title).toBe('Validador visual (juiz)')
    expect(tpl?.form.accessMode).toBe('prompt_only')
  })

  it('disables incompatible controls in prompt_only and submits access_mode to the API', async () => {
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
              name: 'juiz-visual',
              description: 'Compara screenshots',
              prompt: 'Compare referência e resultado.',
              provider: null,
              models: [],
              tools: null,
              disallowed_tools: [],
              effort: null,
              permission_mode: null,
              max_turns: null,
              memory: 'project',
              isolation: null,
              access_mode: 'prompt_only',
              enabled: true,
              updated_at: '2026-11-25T12:00:00Z',
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

    const accessSelect = screen.getByLabelText('Modo de acesso') as HTMLSelectElement
    expect(accessSelect.value).toBe('full')
    expect(Array.from(accessSelect.options).map((o) => o.value)).toEqual(['full', 'read_only', 'prompt_only'])

    const toolsInput = screen.getByLabelText('Ferramentas permitidas') as HTMLInputElement
    const disallowedInput = screen.getByLabelText('Ferramentas proibidas') as HTMLInputElement
    const permissionSelect = screen.getByLabelText('Permissão') as HTMLSelectElement
    const isolationSelect = screen.getByLabelText('Isolamento') as HTMLSelectElement

    expect(toolsInput.disabled).toBe(false)
    expect(isolationSelect.disabled).toBe(false)

    await user.selectOptions(accessSelect, 'prompt_only')
    expect(toolsInput.disabled).toBe(true)
    expect(disallowedInput.disabled).toBe(true)
    expect(permissionSelect.disabled).toBe(true)
    expect(isolationSelect.disabled).toBe(true)

    await user.type(screen.getByLabelText('Nome'), 'juiz-visual')
    await user.type(screen.getByLabelText('Quando usar'), 'Compara screenshots')
    await user.type(screen.getByLabelText('Instruções'), 'Compare referência e resultado.')

    await user.click(screen.getByRole('button', { name: 'Criar agente' }))

    await waitFor(() => {
      expect(submittedBody).not.toBeNull()
    })
    expect(submittedBody).toMatchObject({
      name: 'juiz-visual',
      access_mode: 'prompt_only',
      tools: null,
      disallowed_tools: [],
      permission_mode: null,
      isolation: null,
    })
  })
})
