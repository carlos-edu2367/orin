import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import type { MemoryReviewItem } from '../../../api/memory'
import { MemoryReviewTab } from '../MemoryReviewTab'

function baseItem(overrides: Partial<MemoryReviewItem>): MemoryReviewItem {
  return {
    memory_id: 'mem_1',
    fact: 'Fato exemplo',
    tags: [],
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
    trust: 3,
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
    ...overrides,
  }
}

describe('MemoryReviewTab', () => {
  it('switches between Ativas, Para revisar, and Historico tabs, filters by agent_key, approves, resolves conflicts, and opens history drawer', async () => {
    const user = userEvent.setup()

    const activeSql = baseItem({
      memory_id: 'mem_active_sql',
      fact: 'Usar indices parciais no Postgres.',
      scope_type: 'agent',
      agent_key: 'revisor-sql',
      status: 'active',
    })
    const quarantined = baseItem({
      memory_id: 'mem_quar',
      fact: 'Trecho vindo de pagina web.',
      status: 'quarantined',
      source_kind: 'web',
      review_kind: 'quarantined',
    })
    const conflictOld = baseItem({
      memory_id: 'mem_old',
      fact: 'Usar pnpm no projeto.',
      status: 'active',
    })
    const conflictNew = baseItem({
      memory_id: 'mem_conf',
      fact: 'Usar npm no projeto.',
      status: 'proposed',
      source_kind: 'agent',
      review_kind: 'conflict',
      conflicting_memory: conflictOld,
    })
    const historyInvalid = baseItem({
      memory_id: 'mem_inv',
      fact: 'Preferencia antiga recuperavel.',
      status: 'invalid',
      can_restore: true,
    })
    const historyDeleted = baseItem({
      memory_id: 'mem_del',
      fact: '',
      status: 'deleted',
      can_restore: false,
    })

    const request = vi.fn(
      async (opts: {
        path: string
        method?: string
        query?: Record<string, unknown>
        body?: unknown
        parse: (v: unknown) => unknown
      }) => {
        if (opts.path === '/v1/memory/review') {
          const status = opts.query?.status
          const agentKey = opts.query?.agent_key
          let items: MemoryReviewItem[] = []
          if (status === 'active') {
            items = agentKey === 'revisor-sql' ? [activeSql] : [conflictOld, activeSql]
          } else if (status === 'pending') {
            items = [conflictNew, quarantined]
          } else if (status === 'history') {
            items = [historyInvalid, historyDeleted]
          }
          return opts.parse({
            items,
            counts: {
              active: 2,
              pending: 2,
              proposed: 1,
              quarantined: 1,
              conflicts: 1,
              history: 2,
            },
            agent_keys: ['revisor-sql'],
          })
        }
        if (opts.path === '/v1/memory/mem_quar/approve' && opts.method === 'POST') {
          return opts.parse({ memory: { ...quarantined, status: 'active' }, status: 'active' })
        }
        if (opts.path === '/v1/memory/mem_conf/resolve-conflict' && opts.method === 'POST') {
          return opts.parse({
            memory: { ...conflictNew, status: 'active' },
            status: 'active',
            resolution: 'keep_new',
          })
        }
        if (opts.path === '/v1/memory/mem_inv/restore' && opts.method === 'POST') {
          return opts.parse({
            memory: { ...historyInvalid, status: 'active', can_restore: false },
            status: 'active',
          })
        }
        if (opts.path === '/v1/memory/mem_del/history') {
          return opts.parse({
            memory_id: 'mem_del',
            memory: historyDeleted,
            events: [
              {
                id: 1,
                memory_id: 'mem_del',
                event: 'created',
                old_text: null,
                new_text: 'Criada inicialmente',
                related_id: null,
                actor: 'user:user-1',
                conversation_id: null,
                turn_id: null,
                created_at: '2026-11-20T10:00:00Z',
              },
              {
                id: 2,
                memory_id: 'mem_del',
                event: 'forgotten',
                old_text: 'NAO_DEVE_APARECER',
                new_text: null,
                related_id: null,
                actor: 'user:user-1',
                conversation_id: null,
                turn_id: null,
                created_at: '2026-11-21T10:00:00Z',
              },
            ],
          })
        }
        throw new Error(`Unexpected request: ${opts.method ?? 'GET'} ${opts.path}`)
      },
    )

    const client = {
      request,
      createMutationIntent: vi.fn(() => Object.freeze({ idempotencyKey: 'k-1' })),
    } as unknown as ApiClient

    const { container } = render(<MemoryReviewTab client={client} />)

    // Active tab loads initially
    await waitFor(() => {
      expect(screen.getByTestId('memory-review-item-mem_active_sql')).toBeInTheDocument()
    })

    // Filter by agent_key
    await user.selectOptions(screen.getByTestId('memory-agent-filter'), 'revisor-sql')
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          path: '/v1/memory/review',
          query: expect.objectContaining({ status: 'active', agent_key: 'revisor-sql' }),
        }),
      )
    })

    // Switch to "Para revisar"
    await user.click(screen.getByRole('tab', { name: /Para revisar/i }))
    await waitFor(() => {
      expect(screen.getByTestId('memory-conflict-card-mem_conf')).toBeInTheDocument()
      expect(screen.getByTestId('memory-review-item-mem_quar')).toBeInTheDocument()
    })

    // Approve quarantined item
    await user.click(screen.getByTestId('memory-approve-mem_quar'))
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/v1/memory/mem_quar/approve', method: 'POST' }),
      )
    })

    // Resolve conflict with "Ficar com a nova"
    await user.click(screen.getByRole('button', { name: 'Ficar com a nova' }))
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          path: '/v1/memory/mem_conf/resolve-conflict',
          method: 'POST',
          body: { resolution: 'keep_new', conflicting_memory_id: 'mem_old' },
        }),
      )
    })

    // Switch to "Histórico"
    await user.click(screen.getByRole('tab', { name: /Histórico/i }))
    await waitFor(() => {
      expect(screen.getByTestId('memory-restore-mem_inv')).toBeInTheDocument()
      expect(screen.queryByTestId('memory-restore-mem_del')).not.toBeInTheDocument()
    })

    // Restore recoverable invalid memory
    await user.click(screen.getByTestId('memory-restore-mem_inv'))
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/v1/memory/mem_inv/restore', method: 'POST' }),
      )
    })

    // Open history drawer on deleted tombstone: forgotten event must never expose old_text
    await user.click(screen.getByTestId('memory-history-btn-mem_del'))
    await waitFor(() => {
      expect(screen.getByTestId('memory-history-drawer')).toBeInTheDocument()
      expect(screen.getByTestId('memory-history-event-forgotten')).toBeInTheDocument()
    })
    expect(screen.getByTestId('memory-history-drawer')).not.toHaveTextContent('NAO_DEVE_APARECER')
    expect(screen.getByTestId('memory-history-erased-note')).toBeInTheDocument()

    expect(container.textContent).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})
