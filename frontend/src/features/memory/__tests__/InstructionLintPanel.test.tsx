import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import type { LintReport } from '../../../api/memoryLint'
import { InstructionLintPanel } from '../InstructionLintPanel'

function createMockClient(handler: (options: { path: string; method?: string; body?: unknown }) => LintReport) {
  const request = vi.fn(async (options: { path: string; method?: string; body?: unknown; parse: (value: unknown) => unknown }) => {
    const payload = handler(options)
    return options.parse(payload)
  })
  const createMutationIntent = vi.fn(() => Object.freeze({ idempotencyKey: 'lint-key-1' }))
  return {
    client: { request, createMutationIntent } as unknown as ApiClient,
    request,
  }
}

describe('InstructionLintPanel', () => {
  it('loads dry_run cost estimate before clicking Verificar agora and lists contradictions on live run', async () => {
    const user = userEvent.setup()
    const { client, request } = createMockClient((opts) => {
      const body = (opts.body ?? {}) as { dry_run?: boolean }
      if (body.dry_run) {
        return {
          contradictions: [],
          files_checked: ['ORIN.md'],
          memories_checked: 2,
          estimated_calls: 1,
          dry_run: true,
          warning: null,
        }
      }
      return {
        contradictions: [
          {
            memory_id: 'mem_1',
            memory_fact: 'Para subagentes usar claude-sonnet-5-5.',
            file: 'ORIN.md',
            line: 3,
            quoted_line: 'Use claude-sonnet-4-6 em subagentes.',
            reason: 'ORIN.md exige claude-sonnet-4-6, mas a memória indica claude-sonnet-5-5.',
          },
        ],
        files_checked: ['ORIN.md'],
        memories_checked: 2,
        estimated_calls: 1,
        dry_run: false,
        warning: null,
      }
    })

    const { container } = render(<InstructionLintPanel client={client} projectId="proj-1" />)

    await waitFor(() => {
      expect(screen.getByTestId('instruction-lint-estimate')).toHaveTextContent(
        'Estimativa: 1 chamada(s) de modelo',
      )
    })

    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0][0]).toMatchObject({
      path: '/v1/memory/lint',
      method: 'POST',
      body: { dry_run: true, project_id: 'proj-1' },
    })

    await user.click(screen.getByTestId('instruction-lint-run-button'))

    await waitFor(() => {
      expect(screen.getByTestId('instruction-lint-results')).toBeInTheDocument()
    })

    expect(request).toHaveBeenCalledTimes(2)
    expect(request.mock.calls[1][0]).toMatchObject({
      path: '/v1/memory/lint',
      method: 'POST',
      body: { dry_run: false, project_id: 'proj-1' },
    })

    const item = screen.getByTestId('instruction-lint-item')
    expect(item).toHaveTextContent('ORIN.md:3')
    expect(item).toHaveTextContent('Use claude-sonnet-4-6 em subagentes.')
    expect(item).toHaveTextContent('Para subagentes usar claude-sonnet-5-5.')
    expect(item).toHaveTextContent('ORIN.md exige claude-sonnet-4-6')

    // Must not contain emoji characters
    expect(container.textContent).not.toMatch(/\p{Extended_Pictographic}/u)
  })

  it('shows warning when no model is configured', async () => {
    const user = userEvent.setup()
    const { client } = createMockClient((opts) => {
      const body = (opts.body ?? {}) as { dry_run?: boolean }
      if (body.dry_run) {
        return {
          contradictions: [],
          files_checked: ['CLAUDE.md'],
          memories_checked: 1,
          estimated_calls: 1,
          dry_run: true,
          warning: null,
        }
      }
      return {
        contradictions: [],
        files_checked: ['CLAUDE.md'],
        memories_checked: 1,
        estimated_calls: 1,
        dry_run: false,
        warning: 'Nenhum modelo configurado ou disponível para executar a verificação de contradições.',
      }
    })

    render(<InstructionLintPanel client={client} />)

    await waitFor(() => {
      expect(screen.getByTestId('instruction-lint-estimate')).toHaveTextContent(
        'Estimativa: 1 chamada(s) de modelo',
      )
    })

    await user.click(screen.getByTestId('instruction-lint-run-button'))

    await waitFor(() => {
      expect(screen.getByTestId('instruction-lint-warning')).toHaveTextContent(
        /Nenhum modelo configurado/i,
      )
    })
  })

  it('shows empty state when live run finds zero contradictions', async () => {
    const user = userEvent.setup()
    const { client } = createMockClient((opts) => {
      const body = (opts.body ?? {}) as { dry_run?: boolean }
      return {
        contradictions: [],
        files_checked: ['ORIN.md'],
        memories_checked: 1,
        estimated_calls: body.dry_run ? 1 : 1,
        dry_run: Boolean(body.dry_run),
        warning: null,
      }
    })

    render(<InstructionLintPanel client={client} />)

    await waitFor(() => {
      expect(screen.getByTestId('instruction-lint-estimate')).toHaveTextContent(
        'Estimativa: 1 chamada(s) de modelo',
      )
    })

    await user.click(screen.getByTestId('instruction-lint-run-button'))

    await waitFor(() => {
      expect(screen.getByTestId('instruction-lint-empty')).toHaveTextContent(
        /Nenhuma contradição encontrada/i,
      )
    })
  })
})
