import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../../src/features/conversations/activityTypes'
import { orderAgentTree } from '../../src/features/conversations/agentTree'
import { TeamActivityView } from '../../src/features/conversations/TeamActivityView'

afterEach(() => {
  cleanup()
})

type Row = { id: string; parent: string | null }

function ordered(rows: Row[]): string[] {
  return orderAgentTree(rows, (row) => row.id, (row) => row.parent).map(({ item, depth }) => `${'-'.repeat(depth)}${item.id}`)
}

describe('orderAgentTree', () => {
  it('lists each agent right after the one that started it, with its depth', () => {
    expect(ordered([
      { id: 'a', parent: 'main' },
      { id: 'b', parent: 'main' },
      { id: 'a1', parent: 'a' },
      { id: 'a1x', parent: 'a1' },
      { id: 'b1', parent: 'b' },
    ])).toEqual(['a', '-a1', '--a1x', 'b', '-b1'])
  })

  it('keeps an agent whose parent is not listed at the top level', () => {
    expect(ordered([{ id: 'orphan', parent: 'gone' }, { id: 'root', parent: null }])).toEqual(['orphan', 'root'])
  })

  it('still shows every agent when the data has a cycle', () => {
    expect(ordered([{ id: 'x', parent: 'y' }, { id: 'y', parent: 'x' }]).sort()).toEqual(['-y', 'x'].sort())
  })
})

describe('TeamActivityView agent tree', () => {
  it('puts a subagent started by another subagent under it, indented', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-1', cursor: '1', type: 'tool.started', kind: 'tool', state: 'working',
        agentId: 'agent-lead', agentName: 'Coordenador', parentAgentId: 'agent:conv:main', summary: 'Dividindo o trabalho',
      },
      {
        eventId: 'ev-2', cursor: '2', type: 'tool.started', kind: 'tool', state: 'working',
        agentId: 'agent-other', agentName: 'Outro', parentAgentId: 'agent:conv:main', summary: 'Trabalho paralelo',
      },
      {
        eventId: 'ev-3', cursor: '3', type: 'tool.started', kind: 'tool', state: 'working',
        agentId: 'agent-verifier', agentName: 'Verificador', parentAgentId: 'agent-lead', summary: 'Rodando os testes',
      },
    ]

    render(<TeamActivityView events={events} />)

    const cards = Array.from(screen.getByTestId('team-agents-list').querySelectorAll('[data-testid^="team-agent-card-"]'))
    expect(cards.map((card) => card.getAttribute('data-testid'))).toEqual([
      'team-agent-card-agent-lead',
      'team-agent-card-agent-verifier',
      'team-agent-card-agent-other',
    ])
    const verifier = screen.getByTestId('team-agent-card-agent-verifier')
    expect(verifier.getAttribute('data-depth')).toBe('1')
    expect(verifier.className).toContain('team-agent-card--nested')
    expect(screen.getByTestId('team-agent-card-agent-lead').getAttribute('data-depth')).toBe('0')
  })
})
