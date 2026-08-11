import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { createSkill, getAgentSkills, getSkill, listSkillAgents, listSkills, setAgentSkills } from '../../src/api/skills'

describe('Skills API client', () => {
  it('requests a compact filtered skills list without putting the query in the path argument', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      items: [{
        id: 'systematic-debugging',
        name: 'Systematic Debugging',
        description: 'Investigate a software failure with evidence.',
        version: '1.0.0',
        tags: ['debugging', 'testing'],
        source: 'system',
        available: true,
      }],
      next_cursor: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const result = await listSkills(client, { query: 'debug', source: 'system' })

    expect(result.items).toEqual([{
      id: 'systematic-debugging',
      name: 'Systematic Debugging',
      description: 'Investigate a software failure with evidence.',
      version: '1.0.0',
      tags: ['debugging', 'testing'],
      source: 'system',
      available: true,
    }])
    expect(String(fetchImpl.mock.calls[0][0])).toBe('/v1/skills?query=debug&source=system')
  })

  it('reads a detail payload and sends only authorable fields when creating a skill', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: 'systematic-debugging', name: 'Systematic Debugging', description: 'Investigate a software failure with evidence.',
        version: '1.0.0', tags: ['debugging'], source: 'system', available: true,
        instructions: '# Workflow\n\n1. Reproduce the issue.', dependencies: ['testing'], requires_tools: ['run_command'],
        versions: ['1.0.0'],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: 'review', name: 'Review', description: 'Review a change.', version: '1.0.0', tags: ['quality'], source: 'user', available: true,
      }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const detail = await getSkill(client, 'systematic-debugging')
    const created = await createSkill(client, {
      name: 'Review', description: 'Review a change.', version: '1.0.0', tags: ['quality'], instructions: '# Review',
    })

    expect(detail.instructions).toContain('Reproduce the issue')
    expect(detail.dependencies).toEqual(['testing'])
    expect(created.id).toBe('review')
    const [url, init] = fetchImpl.mock.calls[1]
    expect(String(url)).toBe('/v1/skills')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      name: 'Review', description: 'Review a change.', version: '1.0.0', tags: ['quality'], instructions: '# Review',
    })
  })

  it('reads and saves an agent skill mode, and lists the agents using a skill', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ mode: 'pinned', items: [debugging()] }))
      .mockResolvedValueOnce(json({ mode: 'auto', items: [] }))
      .mockResolvedValueOnce(json({ items: [{ agent_id: 'agent:main', mode: 'pinned' }] }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const association = await getAgentSkills(client, 'agent:main')
    const saved = await setAgentSkills(client, 'agent:main', { mode: 'auto', skill_ids: [] })
    const usedBy = await listSkillAgents(client, 'systematic-debugging')

    expect(association).toEqual({ mode: 'pinned', items: [debugging()] })
    expect(saved).toEqual({ mode: 'auto', items: [] })
    expect(usedBy).toEqual([{ agent_id: 'agent:main', mode: 'pinned' }])
    expect(String(fetchImpl.mock.calls[0][0])).toBe('/v1/agents/agent%3Amain/skills')
    expect(String(fetchImpl.mock.calls[2][0])).toBe('/v1/skills/systematic-debugging/agents')
    expect(JSON.parse(String(fetchImpl.mock.calls[1][1]?.body))).toEqual({ mode: 'auto', skill_ids: [] })
  })
})

function debugging() {
  return {
    id: 'systematic-debugging', name: 'Systematic Debugging', description: 'Investigate a software failure with evidence.',
    version: '1.0.0', tags: ['debugging', 'testing'], source: 'system', available: true,
  }
}

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
}
