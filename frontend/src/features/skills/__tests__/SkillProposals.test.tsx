import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import {
  approveSkillProposal,
  dismissSkillProposal,
  listSkillProposals,
  updateSkillProposal,
  type SkillProposal,
  type SkillSummary,
  type SkillsClient,
} from '../../../api/skills'
import { SkillsPage } from '../SkillsPage'

const EXISTING_SKILL: SkillSummary = {
  id: 'systematic-debugging',
  name: 'Systematic Debugging',
  description: 'Investigate a software failure with evidence.',
  version: '1.0.0',
  tags: ['debugging'],
  source: 'system',
  available: true,
}

const CREATE_PROPOSAL: SkillProposal = {
  proposal_id: 'prop-1',
  action: 'create',
  target_skill_id: null,
  name: 'Diagnóstico de Migração Alembic',
  description: 'Verifica conflitos de cabeças Alembic e valida downgrade antes do commit.',
  when_to_use: ['Ao criar ou revisar migrações de banco de dados'],
  when_not_to_use: ['Em alterações apenas de frontend'],
  requires_tools: ['Bash', 'Read'],
  tags: ['database', 'alembic'],
  instructions: '## Workflow\n1. Rode alembic heads.\n\n## Validation\n1. Confirme head único.',
  status: 'pending',
  created_at: '2026-11-12T00:00:00Z',
  updated_at: '2026-11-12T00:00:00Z',
  conversation_id: 'conv-123',
}

const EDIT_PROPOSAL: SkillProposal = {
  proposal_id: 'prop-2',
  action: 'edit',
  target_skill_id: 'systematic-debugging',
  name: 'Systematic Debugging',
  description: 'Investigate a software failure with logs and reproduction tests.',
  when_to_use: ['Quando um teste falhar inesperadamente'],
  when_not_to_use: ['Para perguntas conceituais simples'],
  requires_tools: ['Grep'],
  tags: ['debugging', 'testing'],
  instructions: '## Workflow\n1. Colete evidências.\n\n## Validation\n1. Rode a suíte.',
  status: 'pending',
  created_at: '2026-11-12T00:10:00Z',
  updated_at: '2026-11-12T00:10:00Z',
  conversation_id: null,
}

function createMockSkillsClient(
  proposals: SkillProposal[] = [],
  skills: SkillSummary[] = [EXISTING_SKILL],
  overrides: Partial<SkillsClient> = {},
): SkillsClient {
  return {
    list: vi.fn(() => Promise.resolve({ items: skills, next_cursor: null })),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    removeVersion: vi.fn(),
    getAgentSkills: vi.fn(() => Promise.resolve({ mode: 'auto' as const, items: [] })),
    setAgentSkills: vi.fn(),
    listSkillAgents: vi.fn(() => Promise.resolve([])),
    listProposals: vi.fn(() => Promise.resolve({ items: proposals })),
    updateProposal: vi.fn((proposalId, input) => {
      const base = proposals.find((p) => p.proposal_id === proposalId) ?? CREATE_PROPOSAL
      return Promise.resolve({
        ...base,
        ...input,
        updated_at: '2026-11-12T01:00:00Z',
      })
    }),
    approveProposal: vi.fn((proposalId, input) => {
      const base = proposals.find((p) => p.proposal_id === proposalId) ?? CREATE_PROPOSAL
      const merged = { ...base, ...input, status: 'approved' as const }
      return Promise.resolve({
        proposal: merged,
        skill: {
          id: merged.target_skill_id ?? 'diagnostico-migracao-alembic',
          name: merged.name,
          description: merged.description,
          version: '1.0.0',
          tags: merged.tags,
          source: 'user',
          available: true,
        },
      })
    }),
    dismissProposal: vi.fn((proposalId) =>
      Promise.resolve({ proposal_id: proposalId, status: 'dismissed' }),
    ),
    ...overrides,
  }
}

function renderSkillsPage(client: SkillsClient) {
  return render(
    <MemoryRouter initialEntries={['/skills']}>
      <Routes>
        <Route path="/skills" element={<SkillsPage client={client} />} />
        <Route path="/skills/:skillId" element={<SkillsPage client={client} />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SkillProposals API functions', () => {
  it('calls GET, PATCH, POST approve, and DELETE on /v1/skill-proposals endpoints', async () => {
    const approvedSkill: SkillSummary = {
      id: 'diagnostico-migracao-alembic',
      name: CREATE_PROPOSAL.name,
      description: CREATE_PROPOSAL.description,
      version: '1.0.0',
      tags: CREATE_PROPOSAL.tags,
      source: 'user',
      available: true,
    }
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [CREATE_PROPOSAL] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...CREATE_PROPOSAL, description: 'Descrição revisada.' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            proposal: { ...CREATE_PROPOSAL, status: 'approved' },
            skill: approvedSkill,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ proposal_id: 'prop-1', status: 'dismissed' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const api = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const listed = await listSkillProposals(api, { status: 'pending' })
    expect(listed.items).toHaveLength(1)
    expect(listed.items[0].proposal_id).toBe('prop-1')
    expect(String(fetchImpl.mock.calls[0][0])).toBe('/v1/skill-proposals?status=pending')

    const updated = await updateSkillProposal(api, 'prop-1', {
      description: 'Descrição revisada.',
    })
    expect(updated.description).toBe('Descrição revisada.')
    expect(String(fetchImpl.mock.calls[1][0])).toBe('/v1/skill-proposals/prop-1')
    expect(fetchImpl.mock.calls[1][1]?.method).toBe('PATCH')

    const approved = await approveSkillProposal(api, 'prop-1')
    expect(approved.proposal.status).toBe('approved')
    expect(approved.skill.id).toBe('diagnostico-migracao-alembic')
    expect(String(fetchImpl.mock.calls[2][0])).toBe('/v1/skill-proposals/prop-1/approve')
    expect(fetchImpl.mock.calls[2][1]?.method).toBe('POST')

    const dismissed = await dismissSkillProposal(api, 'prop-1')
    expect(dismissed).toEqual({ proposal_id: 'prop-1', status: 'dismissed' })
    expect(String(fetchImpl.mock.calls[3][0])).toBe('/v1/skill-proposals/prop-1')
    expect(fetchImpl.mock.calls[3][1]?.method).toBe('DELETE')
  })
})

describe('SkillsPage — Revisão de Propostas de SKILL.md', () => {
  it('does not render the proposals section when there are no pending proposals', async () => {
    const client = createMockSkillsClient([])
    renderSkillsPage(client)

    await screen.findByText('Systematic Debugging')
    expect(screen.queryByTestId('skill-proposals-section')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: /Sugestões de Habilidades Aprendidas/i }),
    ).not.toBeInTheDocument()
  })

  it('renders pending proposals with badges, metadata, and expandable instructions preview', async () => {
    const user = userEvent.setup()
    const client = createMockSkillsClient([CREATE_PROPOSAL, EDIT_PROPOSAL])
    renderSkillsPage(client)

    const section = await screen.findByTestId('skill-proposals-section')
    expect(section).toBeVisible()
    expect(
      within(section).getByRole('heading', { name: /Sugestões de Habilidades Aprendidas/i }),
    ).toBeVisible()
    expect(within(section).getByText('2 pendentes')).toBeVisible()

    const card1 = within(section).getByTestId('skill-proposal-card-prop-1')
    expect(within(card1).getByText('Diagnóstico de Migração Alembic')).toBeVisible()
    expect(within(card1).getByText('Nova skill')).toBeVisible()
    expect(
      within(card1).getByText(
        'Verifica conflitos de cabeças Alembic e valida downgrade antes do commit.',
      ),
    ).toBeVisible()
    expect(within(card1).getByText('database')).toBeVisible()
    expect(within(card1).getByText('Bash')).toBeVisible()

    const card2 = within(section).getByTestId('skill-proposal-card-prop-2')
    expect(within(card2).getByText('Atualização')).toBeVisible()

    // Expand instructions preview on card 1
    const previewToggle = within(card1).getByRole('button', { name: /Ver instruções propostas/i })
    expect(previewToggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(previewToggle)
    expect(previewToggle).toHaveAttribute('aria-expanded', 'true')
    expect(within(card1).getByText(/Rode alembic heads/i)).toBeVisible()
    expect(
      within(card1).getByText('Ao criar ou revisar migrações de banco de dados'),
    ).toBeVisible()
    expect(within(card1).getByText('Em alterações apenas de frontend')).toBeVisible()
  })

  it('approves a pending proposal, removes it from the pending section, and reloads the skills list', async () => {
    const user = userEvent.setup()
    let currentSkills: SkillSummary[] = [EXISTING_SKILL]
    const approvedSkill: SkillSummary = {
      id: 'diagnostico-migracao-alembic',
      name: CREATE_PROPOSAL.name,
      description: CREATE_PROPOSAL.description,
      version: '1.0.0',
      tags: CREATE_PROPOSAL.tags,
      source: 'user',
      available: true,
    }

    const listMock = vi.fn(() => Promise.resolve({ items: currentSkills, next_cursor: null }))
    const approveMock = vi.fn().mockImplementation(() => {
      currentSkills = [approvedSkill, ...currentSkills]
      return Promise.resolve({
        proposal: { ...CREATE_PROPOSAL, status: 'approved' as const },
        skill: approvedSkill,
      })
    })

    const client = createMockSkillsClient([CREATE_PROPOSAL], currentSkills, {
      list: listMock,
      approveProposal: approveMock,
    })

    renderSkillsPage(client)

    const card = await screen.findByTestId('skill-proposal-card-prop-1')
    const approveBtn = within(card).getByRole('button', { name: 'Aprovar' })
    await user.click(approveBtn)

    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith('prop-1', undefined)
    })

    // Section disappears because there are no more pending proposals
    await waitFor(() => {
      expect(screen.queryByTestId('skill-proposals-section')).not.toBeInTheDocument()
    })

    // Skills list was refreshed and the new skill is visible in the library
    await waitFor(() => {
      expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    })
    expect(await screen.findByRole('link', { name: 'Diagnóstico de Migração Alembic' })).toBeVisible()
  })

  it('edits a pending proposal inline and saves changes via updateProposal', async () => {
    const user = userEvent.setup()
    const client = createMockSkillsClient([CREATE_PROPOSAL])
    renderSkillsPage(client)

    const card = await screen.findByTestId('skill-proposal-card-prop-1')
    await user.click(within(card).getByRole('button', { name: 'Editar' }))

    const nameInput = within(card).getByLabelText('Nome da habilidade')
    const descInput = within(card).getByLabelText('Descrição da proposta')
    const instructionsInput = within(card).getByLabelText('Instruções (SKILL.md)')

    await user.clear(nameInput)
    await user.type(nameInput, 'Diagnóstico Alembic Revisado')
    await user.clear(descInput)
    await user.type(descInput, 'Valida migrações com segurança em produção.')
    await user.clear(instructionsInput)
    await user.type(instructionsInput, '## Workflow\n1. Teste upgrade e downgrade.\n\n## Validation\n1. OK.')

    await user.click(within(card).getByRole('button', { name: 'Salvar alterações' }))

    await waitFor(() => {
      expect(client.updateProposal).toHaveBeenCalledWith(
        'prop-1',
        expect.objectContaining({
          name: 'Diagnóstico Alembic Revisado',
          description: 'Valida migrações com segurança em produção.',
          instructions: '## Workflow\n1. Teste upgrade e downgrade.\n\n## Validation\n1. OK.',
        }),
      )
    })

    expect(await within(card).findByText('Diagnóstico Alembic Revisado')).toBeVisible()
    expect(within(card).getByText('Valida migrações com segurança em produção.')).toBeVisible()
  })

  it('dismisses a pending proposal and removes it from the list', async () => {
    const user = userEvent.setup()
    const client = createMockSkillsClient([CREATE_PROPOSAL, EDIT_PROPOSAL])
    renderSkillsPage(client)

    const section = await screen.findByTestId('skill-proposals-section')
    const card1 = within(section).getByTestId('skill-proposal-card-prop-1')

    await user.click(within(card1).getByRole('button', { name: 'Descartar' }))

    await waitFor(() => {
      expect(client.dismissProposal).toHaveBeenCalledWith('prop-1')
    })

    await waitFor(() => {
      expect(screen.queryByTestId('skill-proposal-card-prop-1')).not.toBeInTheDocument()
    })
    // Second proposal remains visible and count updates to 1
    expect(within(section).getByTestId('skill-proposal-card-prop-2')).toBeVisible()
    expect(within(section).getByText('1 pendente')).toBeVisible()
  })
})
