import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { SkillsSection } from '../../src/features/skills/SkillsSection'

describe('SkillsSection', () => {
  it('renders as content without an app shell or topbar', async () => {
    const client = { list: vi.fn().mockResolvedValue({ items: [], next_cursor: null }), get: vi.fn(), create: vi.fn(), update: vi.fn(), removeVersion: vi.fn(), getAgentSkills: vi.fn().mockResolvedValue({ mode: 'auto', items: [] }), setAgentSkills: vi.fn(), listSkillAgents: vi.fn().mockResolvedValue([]) }
    render(<MemoryRouter initialEntries={['/settings/skills']}><SkillsSection client={client} /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'Skills' })).toBeInTheDocument()
    expect(document.querySelector('.app-shell')).toBeNull()
    expect(screen.getByRole('searchbox', { name: 'Buscar skills' })).toBeEnabled()
  })
})
