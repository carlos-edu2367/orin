import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { CreateSkillInput, SkillDetail, SkillListOptions, SkillSummary, SkillsClient } from '../../src/api/skills'
import { SkillsPage } from '../../src/features/skills/SkillsPage'

const debugging: SkillSummary = {
  id: 'systematic-debugging', name: 'Systematic Debugging',
  description: 'Investigate a software failure with evidence.', version: '1.0.0',
  tags: ['debugging', 'testing'], source: 'system', available: true,
}

const pdf: SkillSummary = {
  id: 'pdf', name: 'PDF', description: 'Read and create PDFs.', version: '1.0.0',
  tags: ['documents'], source: 'system', available: true,
}

function detail(): SkillDetail {
  return {
    ...debugging, instructions: '# Workflow\n\n1. Reproduce the issue.', dependencies: ['testing'],
    requires_tools: ['run_command'], versions: ['1.0.0'],
  }
}

function skillsClient(overrides: Partial<SkillsClient> = {}): SkillsClient {
  return {
    list: vi.fn((options: SkillListOptions = {}) => Promise.resolve({
      items: [debugging, pdf].filter((skill) => !options.query || `${skill.name} ${skill.description}`.toLowerCase().includes(options.query.toLowerCase())),
      next_cursor: null,
    })),
    get: vi.fn(() => Promise.resolve(detail())),
    create: vi.fn((input: CreateSkillInput) => Promise.resolve({
      id: 'review', name: input.name, description: input.description, version: input.version, tags: input.tags, source: 'user', available: true,
    })),
    update: vi.fn(),
    getAgentSkills: vi.fn(() => Promise.resolve({ mode: 'auto' as const, items: [] })),
    setAgentSkills: vi.fn(),
    listSkillAgents: vi.fn(() => Promise.resolve([])),
    ...overrides,
  }
}

function renderPage(client: SkillsClient, entry = '/skills') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/skills" element={<SkillsPage client={client} />} />
        <Route path="/skills/:skillId" element={<SkillsPage client={client} />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SkillsPage', () => {
  it('filters compact skill rows without rendering instructions', async () => {
    const client = skillsClient()
    const user = userEvent.setup()
    renderPage(client)

    await screen.findByText('Systematic Debugging')
    await user.type(screen.getByRole('searchbox', { name: 'Buscar skills' }), 'debug')

    expect(await screen.findByText('Systematic Debugging')).toBeVisible()
    expect(screen.queryByText('PDF')).not.toBeInTheDocument()
    expect(screen.queryByText('Workflow')).not.toBeInTheDocument()
  })

  it('keeps instructions in a detail disclosure until the user requests them', async () => {
    const client = skillsClient()
    const user = userEvent.setup()
    renderPage(client, '/skills/systematic-debugging')

    expect(await screen.findByRole('heading', { name: 'Systematic Debugging' })).toBeVisible()
    expect(screen.queryByText('Workflow')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Instruções' }))

    expect(screen.getByText(/Workflow/)).toBeVisible()
    expect(screen.getByText('testing')).toBeVisible()
  })

  it('creates a new skill from the focused form and returns to the compact list', async () => {
    const client = skillsClient()
    const user = userEvent.setup()
    renderPage(client)

    await screen.findByText('Systematic Debugging')
    await user.click(screen.getByRole('button', { name: 'Criar skill' }))
    await user.type(screen.getByLabelText('Nome'), 'Review')
    await user.type(screen.getByLabelText('Descrição'), 'Review a change.')
    await user.type(screen.getByLabelText('Instruções'), '# Review')
    await user.type(screen.getByLabelText('Tags'), 'quality')
    await user.click(screen.getByRole('button', { name: 'Campos avançados' }))
    await user.click(screen.getByRole('button', { name: 'Salvar skill' }))

    await waitFor(() => expect(client.create).toHaveBeenCalledWith({
      name: 'Review', description: 'Review a change.', version: '1.0.0', tags: ['quality'], instructions: '# Review',
    }))
    expect(await screen.findByText('Review')).toBeVisible()
  })

  it('shows the required instructions field before advanced creation options', async () => {
    const user = userEvent.setup()
    renderPage(skillsClient())

    await screen.findByText('Systematic Debugging')
    await user.click(screen.getByRole('button', { name: 'Criar skill' }))

    expect(screen.getByLabelText('Instruções')).toBeVisible()
    expect(screen.getByLabelText('Instruções')).toHaveAttribute('required')
    expect(screen.getByLabelText('Tags')).toBeVisible()
    expect(screen.queryByLabelText('Versão')).not.toBeInTheDocument()
  })

  it('retries a failed library load and exposes the recovered list', async () => {
    const list = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ items: [debugging], next_cursor: null })
    const client = skillsClient({ list })
    const user = userEvent.setup()
    renderPage(client)

    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível carregar as skills.')
    await user.click(screen.getByRole('button', { name: 'Tentar novamente' }))

    expect(await screen.findByText('Systematic Debugging')).toBeVisible()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('loads the next skills page and appends its compact rows', async () => {
    const list = vi.fn((options: SkillListOptions = {}) => Promise.resolve(options.cursor === 'cursor-2'
      ? { items: [pdf], next_cursor: null }
      : { items: [debugging], next_cursor: 'cursor-2' }))
    const user = userEvent.setup()
    renderPage(skillsClient({ list }))

    await screen.findByText('Systematic Debugging')
    await user.click(screen.getByRole('button', { name: 'Carregar mais skills' }))

    expect(await screen.findByText('PDF')).toBeVisible()
    expect(list).toHaveBeenLastCalledWith({ query: '', source: undefined, cursor: 'cursor-2' }, expect.any(AbortSignal))
    expect(screen.queryByRole('button', { name: 'Carregar mais skills' })).not.toBeInTheDocument()
  })

  it('keeps the cursor after a failed next page and retries it without losing the loaded rows', async () => {
    let nextPageAttempts = 0
    const list = vi.fn((options: SkillListOptions = {}) => {
      if (!options.cursor) return Promise.resolve({ items: [debugging], next_cursor: 'cursor-2' })
      nextPageAttempts += 1
      if (nextPageAttempts === 1) return Promise.reject(new Error('offline'))
      return Promise.resolve({ items: [pdf], next_cursor: null })
    })
    const user = userEvent.setup()
    renderPage(skillsClient({ list }))

    await screen.findByText('Systematic Debugging')
    await user.click(screen.getByRole('button', { name: 'Carregar mais skills' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível carregar mais skills.')
    expect(screen.getByText('Systematic Debugging')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Tentar carregar mais' }))

    expect(await screen.findByText('PDF')).toBeVisible()
    expect(list).toHaveBeenCalledTimes(3)
  })

  it('clears a stale next-page error when a new base search replaces pagination state', async () => {
    const list = vi.fn((options: SkillListOptions = {}) => {
      if (options.query === 'debug') return Promise.resolve({ items: [debugging], next_cursor: null })
      if (options.cursor) return Promise.reject(new Error('offline'))
      return Promise.resolve({ items: [debugging], next_cursor: 'cursor-2' })
    })
    const user = userEvent.setup()
    renderPage(skillsClient({ list }))

    await screen.findByText('Systematic Debugging')
    await user.click(screen.getByRole('button', { name: 'Carregar mais skills' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível carregar mais skills.')
    await user.type(screen.getByRole('searchbox', { name: 'Buscar skills' }), 'debug')

    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: 'debug', source: undefined }, expect.any(AbortSignal)))
    expect(screen.queryByText('Não foi possível carregar mais skills.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Carregar mais skills' })).not.toBeInTheDocument()
  })

  it('retries a failed detail request', async () => {
    const get = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(detail())
    const user = userEvent.setup()
    renderPage(skillsClient({ get }), '/skills/systematic-debugging')

    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível carregar esta skill.')
    await user.click(screen.getByRole('button', { name: 'Tentar novamente' }))

    expect(await screen.findByRole('heading', { name: 'Systematic Debugging' })).toBeVisible()
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('edits a skill from its detail view and shows the accepted revision', async () => {
    const updated = { ...detail(), description: 'Investigate a failure with an updated workflow.' }
    const update = vi.fn().mockResolvedValue(updated)
    const user = userEvent.setup()
    renderPage(skillsClient({ update }), '/skills/systematic-debugging')

    await screen.findByRole('heading', { name: 'Systematic Debugging' })
    await user.click(screen.getByRole('button', { name: 'Editar skill' }))
    const description = screen.getByLabelText('Descrição')
    await user.clear(description)
    await user.type(description, updated.description)
    await user.click(screen.getByRole('button', { name: 'Salvar alterações' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith('systematic-debugging', expect.objectContaining({
      description: updated.description,
      instructions: '# Workflow\n\n1. Reproduce the issue.',
    })))
    expect(await screen.findByText(updated.description)).toBeVisible()
  })

  it('configures an agent for pinned skills and saves the selected ids', async () => {
    const getAgentSkills = vi.fn().mockResolvedValue({ mode: 'pinned', items: [debugging] })
    const setAgentSkills = vi.fn().mockResolvedValue({ mode: 'pinned', items: [debugging, pdf] })
    const user = userEvent.setup()
    renderPage(skillsClient({ getAgentSkills, setAgentSkills }))

    const panel = screen.getByRole('region', { name: 'Skills do agente' })
    await user.click(within(panel).getByRole('button', { name: 'Carregar configuração' }))
    expect(await within(panel).findByRole('checkbox', { name: 'Systematic Debugging' })).toBeChecked()
    expect(within(panel).getByRole('radio', { name: 'Skills fixadas' })).toBeChecked()
    await user.click(within(panel).getByRole('checkbox', { name: 'PDF' }))
    await user.click(within(panel).getByRole('button', { name: 'Salvar configuração' }))

    await waitFor(() => expect(setAgentSkills).toHaveBeenCalledWith('agent:main', { mode: 'pinned', skill_ids: ['systematic-debugging', 'pdf'] }))
  })

  it('retains an associated skill outside the loaded library page so it can be removed', async () => {
    const getAgentSkills = vi.fn().mockResolvedValue({ mode: 'pinned', items: [pdf] })
    const setAgentSkills = vi.fn().mockResolvedValue({ mode: 'pinned', items: [] })
    const user = userEvent.setup()
    renderPage(skillsClient({ list: vi.fn(() => Promise.resolve({ items: [debugging], next_cursor: null })), getAgentSkills, setAgentSkills }))

    const panel = screen.getByRole('region', { name: 'Skills do agente' })
    await user.click(within(panel).getByRole('button', { name: 'Carregar configuração' }))
    const associatedPdf = await within(panel).findByRole('checkbox', { name: 'PDF' })
    expect(associatedPdf).toBeChecked()
    await user.click(associatedPdf)
    await user.click(within(panel).getByRole('button', { name: 'Salvar configuração' }))

    await waitFor(() => expect(setAgentSkills).toHaveBeenCalledWith('agent:main', { mode: 'pinned', skill_ids: [] }))
  })

  it('shows which agents use a skill when the association endpoint returns items', async () => {
    const listSkillAgents = vi.fn().mockResolvedValue([{ agent_id: 'agent:main', mode: 'pinned' }])
    renderPage(skillsClient({ listSkillAgents }), '/skills/systematic-debugging')

    await screen.findByText('Usada por')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Usada por' }))
    expect(screen.getByText(/agent:main/)).toBeVisible()
  })
})
