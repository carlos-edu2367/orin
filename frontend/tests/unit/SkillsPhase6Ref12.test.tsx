import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { SkillSummary, SkillsClient } from '../../src/api/skills'
import { SkillsPage } from '../../src/features/skills/SkillsPage'

const SAMPLE_SKILLS: SkillSummary[] = [
  {
    id: 'skill-reports',
    name: 'Criar relatórios',
    description: 'Transforma dados e notas em relatórios claros com resumo executivo.',
    version: '1.0.0',
    tags: ['reports'],
    source: 'system',
    available: true,
  },
  {
    id: 'skill-review',
    name: 'Revisar textos',
    description: 'Corrige clareza, tom e ortografia mantendo a sua voz.',
    version: '1.0.0',
    tags: ['writing'],
    source: 'system',
    available: true,
  },
  {
    id: 'skill-custom',
    name: 'Guia de Estilo',
    description: 'Aplica diretrizes editoriais e padrões de código do projeto.',
    version: '1.2.0',
    tags: ['custom'],
    source: 'user',
    available: true,
  },
]

function createSkillsClientWithItems(
  items: SkillSummary[] = [],
  overrides: Partial<SkillsClient> = {},
): SkillsClient {
  return {
    list: vi.fn(() => Promise.resolve({ items, next_cursor: null })),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    removeVersion: vi.fn(),
    getAgentSkills: vi.fn(() => Promise.resolve({ mode: 'auto' as const, items: [] })),
    setAgentSkills: vi.fn(),
    listSkillAgents: vi.fn(() => Promise.resolve([])),
    ...overrides,
  }
}

function renderSkillsPage(client: SkillsClient = createSkillsClientWithItems()) {
  return render(
    <MemoryRouter initialEntries={['/skills']}>
      <Routes>
        <Route path="/skills" element={<SkillsPage client={client} />} />
        <Route path="/skills/:skillId" element={<SkillsPage client={client} />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SkillsPage Phase 6 (ref-12.png)', () => {
  it('renders header, subtitle, category tabs, searchbox, toolbar buttons, and honest empty state when API returns empty list', async () => {
    renderSkillsPage(createSkillsClientWithItems([]))

    const heading = await screen.findByTestId('skills-heading')
    expect(heading).toHaveTextContent('Habilidades')
    expect(
      screen.getByText('Pacotes de instruções que ensinam o Orin a executar tarefas específicas.'),
    ).toBeVisible()

    const tablist = screen.getByRole('tablist', { name: /Categorias de habilidades/i })
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Prontas para usar',
      'Criadas por você',
      'Da equipe',
    ])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')

    const searchbox = screen.getByRole('searchbox', { name: 'Buscar skills' })
    expect(searchbox).toHaveAttribute('placeholder', 'Buscar habilidades...')

    const createBtn = screen.getByTestId('skills-create-button')
    expect(createBtn).toHaveTextContent('+ Criar habilidade')

    const importBtn = screen.getByTestId('skills-import-button')
    expect(importBtn).toHaveTextContent('Importar arquivo')

    expect(await screen.findByTestId('skills-empty-state')).toBeVisible()
    expect(screen.getByTestId('skills-inspector-empty')).toBeVisible()
    expect(screen.queryByText(/Auryly/i)).not.toBeInTheDocument()
  })

  it('renders API skill cards in the grid and filters by category tab', async () => {
    const user = userEvent.setup()
    const { container } = renderSkillsPage(createSkillsClientWithItems(SAMPLE_SKILLS))

    await screen.findByRole('link', { name: 'Criar relatórios' })
    const grid = container.querySelector('.skills-cards-grid')
    expect(grid).not.toBeNull()

    const gridScope = within(grid as HTMLElement)
    expect(gridScope.getByText('Criar relatórios')).toBeVisible()
    expect(
      gridScope.getByText('Transforma dados e notas em relatórios claros com resumo executivo.'),
    ).toBeVisible()
    expect(gridScope.getByText('Revisar textos')).toBeVisible()
    expect(gridScope.getByText('Guia de Estilo')).toBeVisible()

    expect(gridScope.getAllByText('Instalada')).toHaveLength(2)
    expect(gridScope.getByText('Criada por você')).toBeVisible()

    const firstCard = grid?.querySelector('.skill-card-v6.is-selected')
    expect(firstCard).not.toBeNull()
    expect(firstCard).toHaveTextContent('Criar relatórios')

    // Switch to "Criadas por você" tab
    await user.click(screen.getByTestId('skills-tab-custom'))
    expect(gridScope.getByText('Guia de Estilo')).toBeVisible()
    expect(gridScope.queryByText('Criar relatórios')).not.toBeInTheDocument()
  })

  it('renders the right-hand inspector with real skill details, neutral availability options, and closed instructions accordion by default', async () => {
    const user = userEvent.setup()
    renderSkillsPage(createSkillsClientWithItems(SAMPLE_SKILLS))

    await screen.findByRole('link', { name: 'Criar relatórios' })
    const inspector = await screen.findByTestId('skills-inspector-panel')
    const inspectorScope = within(inspector)

    expect(inspectorScope.getByRole('heading', { name: /Criar relatórios/ })).toBeVisible()
    expect(inspectorScope.getByText('Instalada')).toBeVisible()
    expect(
      inspectorScope.getByText(/Transforma dados e notas em relatórios claros com resumo executivo\./),
    ).toBeVisible()

    const availabilitySelect = inspectorScope.getByLabelText('Disponível em') as HTMLSelectElement
    expect(availabilitySelect).toBeVisible()
    expect(
      Array.from(availabilitySelect.options).map((opt) => opt.textContent),
    ).toEqual(['Todos os projetos', 'Apenas no projeto atual'])

    const confirmToggle = inspectorScope.getByRole('checkbox', {
      name: /Pedir confirmação|Antes de publicar/i,
    })
    expect(confirmToggle).toBeChecked()

    await user.selectOptions(availabilitySelect, 'Apenas no projeto atual')
    expect(availabilitySelect.value).toBe('Apenas no projeto atual')

    await user.click(confirmToggle)
    expect(confirmToggle).not.toBeChecked()

    const quickExample = inspectorScope.getByTestId('skill-quick-example')
    expect(quickExample).toHaveTextContent('Use a habilidade Criar relatórios na tarefa atual')
    expect(within(quickExample).getByRole('button', { name: 'Testar no chat' })).toBeVisible()

    const accordion = inspectorScope.getByTestId('skill-instructions-accordion')
    const trigger = within(accordion).getByRole('button', { name: /Instruções da habilidade/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(
      within(accordion).getByText(/Transforma dados e notas em relatórios claros/i),
    ).toBeVisible()
  })

  it('updates the inspector and collapses instructions accordion when selecting another card', async () => {
    const user = userEvent.setup()
    const { container } = renderSkillsPage(createSkillsClientWithItems(SAMPLE_SKILLS))

    await screen.findByRole('link', { name: 'Criar relatórios' })
    const inspector = await screen.findByTestId('skills-inspector-panel')
    const accordion = within(inspector).getByTestId('skill-instructions-accordion')
    const trigger = within(accordion).getByRole('button', { name: /Instruções da habilidade/i })

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    const grid = container.querySelector('.skills-cards-grid') as HTMLElement
    const cards = grid.querySelectorAll('.skill-card-v6')
    await user.click(cards[2] as HTMLElement)

    expect(within(inspector).getByRole('heading', { name: /Guia de Estilo/ })).toBeVisible()
    expect(within(inspector).getByText('Criada por você')).toBeVisible()
    expect(
      within(inspector).getByText(/Aplica diretrizes editoriais e padrões de código do projeto\./),
    ).toBeVisible()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
})
