import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HooksSection } from '../HooksSection'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('HooksSection — seleção de agente em hooks type: agent (WS-A1)', () => {
  it('exibe select "Agente" (explore / verifier) para hook do tipo agent e grava agent no JSON ao salvar', async () => {
    const savedBodies: Array<Record<string, unknown>> = []
    const initialReport = {
      disabled: false,
      effective: [
        {
          event: 'Stop',
          matcher: '*',
          type: 'agent',
          source: 'user',
          label: 'agent: Verifique os testes',
          timeout: 120,
          agent: 'explore',
        },
      ],
      editable: {
        user: {
          file: '/home/user/.orin/settings.json',
          hooks: {
            Stop: [
              {
                matcher: '*',
                hooks: [{ type: 'agent', prompt: 'Verifique os testes' }],
              },
            ],
          },
        },
      },
      errors: [],
    }

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      if (url.includes('/v1/settings/hooks') && init?.method === 'PUT') {
        const parsed = JSON.parse(String(init.body || '{}')) as Record<string, unknown>
        savedBodies.push(parsed)
        return jsonResponse({
          ...initialReport,
          effective: [{ ...initialReport.effective[0], agent: 'verifier' }],
          editable: {
            user: {
              file: '/home/user/.orin/settings.json',
              hooks: parsed.hooks,
            },
          },
        })
      }
      return jsonResponse(initialReport)
    })

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/settings/hooks']}>
        <HooksSection />
      </MemoryRouter>,
    )

    const agentSelect = await screen.findByLabelText('Agente')
    expect((agentSelect as HTMLSelectElement).value).toBe('explore')

    await user.selectOptions(agentSelect, 'verifier')
    expect((agentSelect as HTMLSelectElement).value).toBe('verifier')

    const textarea = screen.getByLabelText('Hooks deste arquivo (JSON)') as HTMLTextAreaElement
    expect(JSON.parse(textarea.value)).toEqual({
      Stop: [
        {
          matcher: '*',
          hooks: [{ type: 'agent', prompt: 'Verifique os testes', agent: 'verifier' }],
        },
      ],
    })

    await user.click(screen.getByRole('button', { name: 'Salvar' }))

    await waitFor(() => {
      expect(savedBodies).toHaveLength(1)
    })
    expect(savedBodies[0]).toEqual({
      layer: 'user',
      hooks: {
        Stop: [
          {
            matcher: '*',
            hooks: [{ type: 'agent', prompt: 'Verifique os testes', agent: 'verifier' }],
          },
        ],
      },
    })
  })

  it('mostra o select "Agente" no construtor somente quando o tipo selecionado é agent', async () => {
    const emptyReport = {
      disabled: false,
      effective: [],
      editable: {
        user: {
          file: '/home/user/.orin/settings.json',
          hooks: {},
        },
      },
      errors: [],
    }

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(emptyReport))

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/settings/hooks']}>
        <HooksSection />
      </MemoryRouter>,
    )

    const typeSelect = await screen.findByLabelText('Tipo')
    expect(screen.queryByLabelText('Agente')).toBeNull()

    await user.selectOptions(typeSelect, 'agent')
    const agentSelect = screen.getByLabelText('Agente') as HTMLSelectElement
    expect(agentSelect.value).toBe('explore')

    await user.selectOptions(agentSelect, 'verifier')
    await user.type(screen.getByLabelText('Conteúdo do novo hook'), 'Rodar verificador')
    await user.click(screen.getByRole('button', { name: 'Adicionar hook' }))

    const textarea = screen.getByLabelText('Hooks deste arquivo (JSON)') as HTMLTextAreaElement
    expect(JSON.parse(textarea.value)).toEqual({
      Stop: [
        {
          matcher: '',
          hooks: [{ type: 'agent', prompt: 'Rodar verificador', agent: 'verifier' }],
        },
      ],
    })
  })
})
