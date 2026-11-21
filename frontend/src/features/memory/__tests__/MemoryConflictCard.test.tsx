import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { MemoryReviewItem } from '../../../api/memory'
import { MemoryConflictCard } from '../MemoryConflictCard'

function makeItem(overrides: Partial<MemoryReviewItem> = {}): MemoryReviewItem {
  return {
    memory_id: 'mem_new',
    fact: 'O projeto usa npm.',
    tags: [],
    kind: 'fact',
    scope_type: 'project',
    scope_id: 'proj-1',
    project_id: 'proj-1',
    conversation_id: null,
    agent_key: 'revisor-sql',
    status: 'proposed',
    source: 'agent_learned',
    source_kind: 'agent',
    source_ref: null,
    trust: 1,
    confidence: 0.9,
    superseded_by: null,
    valid_from: '2026-11-21T12:00:00Z',
    invalid_at: null,
    hit_count: 0,
    last_used_at: null,
    created_at: '2026-11-21T12:00:00Z',
    updated_at: '2026-11-21T12:00:00Z',
    review_kind: 'conflict',
    can_restore: false,
    conflicting_memory: {
      memory_id: 'mem_old',
      fact: 'O projeto usa pnpm.',
      tags: [],
      kind: 'fact',
      scope_type: 'project',
      scope_id: 'proj-1',
      project_id: 'proj-1',
      conversation_id: null,
      agent_key: null,
      status: 'active',
      source: 'user_explicit',
      source_kind: 'user',
      source_ref: null,
      trust: 3,
      confidence: 1,
      superseded_by: null,
      valid_from: '2026-11-20T12:00:00Z',
      invalid_at: null,
      hit_count: 2,
      last_used_at: null,
      created_at: '2026-11-20T12:00:00Z',
      updated_at: '2026-11-20T12:00:00Z',
      review_kind: null,
      can_restore: false,
      conflicting_memory: null,
    },
    ...overrides,
  }
}

describe('MemoryConflictCard', () => {
  it('displays active and proposed memories side by side with exact three resolution buttons and no emojis', async () => {
    const user = userEvent.setup()
    const onResolve = vi.fn()
    const onOpenHistory = vi.fn()
    const item = makeItem()

    const { container } = render(
      <MemoryConflictCard item={item} onResolve={onResolve} onOpenHistory={onOpenHistory} />,
    )

    expect(screen.getByRole('heading', { name: 'Conflito' })).toBeInTheDocument()
    expect(screen.getByTestId('conflict-existing-memory')).toHaveTextContent('O projeto usa pnpm.')
    expect(screen.getByTestId('conflict-proposed-memory')).toHaveTextContent('O projeto usa npm.')

    await user.click(screen.getByRole('button', { name: 'Ficar com a nova' }))
    expect(onResolve).toHaveBeenLastCalledWith('mem_new', 'keep_new', 'mem_old')

    await user.click(screen.getByRole('button', { name: 'Ficar com a antiga' }))
    expect(onResolve).toHaveBeenLastCalledWith('mem_new', 'keep_old', 'mem_old')

    await user.click(screen.getByRole('button', { name: 'Manter as duas' }))
    expect(onResolve).toHaveBeenLastCalledWith('mem_new', 'keep_both', 'mem_old')

    await user.click(screen.getByRole('button', { name: 'Histórico' }))
    expect(onOpenHistory).toHaveBeenCalledWith('mem_new')

    expect(container.textContent).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})
