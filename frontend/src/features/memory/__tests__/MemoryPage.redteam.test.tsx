import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import { MemoryPage } from '../MemoryPage'

describe('MemoryPage (Red Team #10)', () => {
  it('exposes Histórico button on active cards and inspector and refreshes review counts after create/update/forget', async () => {
    const user = userEvent.setup()

    let activeCount = 1
    let historyCount = 0
    let reviewCalls = 0
    const createdIds: string[] = []

    const request = vi.fn(
      async (opts: {
        path: string
        method?: string
        query?: Record<string, unknown>
        body?: Record<string, unknown>
        parse: (v: unknown) => unknown
      }) => {
        if (opts.path === '/v1/memories' && (!opts.method || opts.method === 'GET')) {
          return opts.parse({
            items: [
              {
                memory_id: 'mem_active_1',
                fact: 'Preferir pytest sem cache no projeto.',
                tags: ['work'],
                scope: 'user',
                project_id: null,
                conversation_id: null,
                created_at: '2026-11-21T10:00:00Z',
                updated_at: '2026-11-21T10:00:00Z',
              },
            ],
            next_cursor: null,
          })
        }
        if (opts.path === '/v1/memories' && opts.method === 'POST') {
          activeCount += 1
          const id = `mem_created_${createdIds.length + 1}`
          createdIds.push(id)
          return opts.parse({
            memory_id: id,
            fact: String(opts.body?.fact ?? ''),
            tags: ['work'],
            scope: 'user',
            project_id: null,
            conversation_id: null,
            created_at: '2026-11-21T11:00:00Z',
            updated_at: '2026-11-21T11:00:00Z',
          })
        }
        if (opts.path.startsWith('/v1/memories/') && opts.method === 'PATCH') {
          return opts.parse({
            memory_id: 'mem_active_1',
            fact: String(opts.body?.fact ?? ''),
            tags: ['work'],
            scope: 'user',
            project_id: null,
            conversation_id: null,
            created_at: '2026-11-21T10:00:00Z',
            updated_at: '2026-11-21T11:05:00Z',
          })
        }
        if (opts.path.startsWith('/v1/memories/') && opts.method === 'DELETE') {
          activeCount = Math.max(0, activeCount - 1)
          historyCount += 1
          return opts.parse(undefined)
        }
        if (opts.path === '/v1/memory/review') {
          reviewCalls += 1
          return opts.parse({
            items: [],
            counts: {
              active: activeCount,
              pending: 0,
              proposed: 0,
              quarantined: 0,
              conflicts: 0,
              history: historyCount,
            },
            agent_keys: [],
          })
        }
        if (opts.path === '/v1/memory/mem_active_1/history') {
          return opts.parse({
            memory_id: 'mem_active_1',
            memory: {
              memory_id: 'mem_active_1',
              fact: 'Preferir pytest sem cache no projeto.',
              tags: ['work'],
              kind: 'fact',
              scope_type: 'user',
              scope_id: 'user-1',
              project_id: null,
              conversation_id: null,
              agent_key: null,
              status: 'active',
              source: 'user_explicit',
              source_kind: 'user',
              source_ref: null,
              trust: 2,
              confidence: 1,
              superseded_by: null,
              valid_from: '2026-11-21T10:00:00Z',
              invalid_at: null,
              hit_count: 0,
              last_used_at: null,
              created_at: '2026-11-21T10:00:00Z',
              updated_at: '2026-11-21T10:00:00Z',
              review_kind: null,
              can_restore: false,
              conflicting_memory: null,
            },
            events: [
              {
                id: 1,
                memory_id: 'mem_active_1',
                event: 'created',
                old_text: null,
                new_text: 'Preferir pytest sem cache no projeto.',
                related_id: null,
                actor: 'user:user-1',
                conversation_id: null,
                turn_id: null,
                created_at: '2026-11-21T10:00:00Z',
              },
            ],
          })
        }
        if (opts.path === '/v1/settings/effective') {
          return opts.parse({
            settings: {
              memory: {
                quarantine: { enabled: true },
                agent: { enabled: false, learnFromVerifiedOnly: true },
                instructionLint: { enabled: false },
              },
            },
          })
        }
        throw new Error(`Unexpected request: ${opts.method ?? 'GET'} ${opts.path}`)
      },
    )

    const client = {
      request,
      createMutationIntent: vi.fn(() => Object.freeze({ idempotencyKey: 'k-rt10' })),
    } as unknown as ApiClient

    render(<MemoryPage embedded client={client} />)

    // Active card renders with the new Histórico button and initial badge count = 1
    await waitFor(() => {
      expect(screen.getByTestId('memory-card-history-mem_active_1')).toBeInTheDocument()
      expect(screen.getByTestId('memory-inspector-history-button')).toBeInTheDocument()
      expect(screen.getByTestId('memory-lifecycle-tab-active')).toHaveTextContent('1')
    })

    // Click Histórico on the active card -> opens MemoryHistoryDrawer
    await user.click(screen.getByTestId('memory-card-history-mem_active_1'))
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/v1/memory/mem_active_1/history' }),
      )
      expect(screen.getByTestId('memory-history-drawer')).toBeInTheDocument()
    })

    // Close drawer
    await user.click(screen.getByRole('button', { name: /fechar/i }))
    await waitFor(() => {
      expect(screen.queryByTestId('memory-history-drawer')).not.toBeInTheDocument()
    })

    // Create a new memory -> triggers reviewRefreshTick and updates Ativas badge to 2
    const callsBeforeCreate = reviewCalls
    await user.click(screen.getByTestId('memory-add-button'))
    await user.type(screen.getByLabelText('Texto da lembrança'), 'Usar uv run python nos scripts.')
    await user.click(screen.getByTestId('memory-save-button'))

    await waitFor(() => {
      expect(reviewCalls).toBeGreaterThan(callsBeforeCreate)
      expect(screen.getByTestId('memory-lifecycle-tab-active')).toHaveTextContent('2')
    })

    // Forget the newly created memory -> triggers reviewRefreshTick and updates Ativas=1 and Histórico=1
    const callsBeforeForget = reviewCalls
    await user.click(screen.getByTestId('memory-forget-button'))

    await waitFor(() => {
      expect(reviewCalls).toBeGreaterThan(callsBeforeForget)
      expect(screen.getByTestId('memory-lifecycle-tab-active')).toHaveTextContent('1')
      expect(screen.getByTestId('memory-lifecycle-tab-history')).toHaveTextContent('1')
    })
  })
})
