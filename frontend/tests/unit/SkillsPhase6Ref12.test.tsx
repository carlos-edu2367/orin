import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { SkillSummary, SkillsClient } from '../../src/api/skills'
import { SkillsPage } from '../../src/features/skills/SkillsPage'

function createEmptySkillsClient(overrides: Partial<SkillsClient> = {}): SkillsClient {
  return {
    list: vi.fn(() => Promise.resolve({ items: [], next_cursor: null })),
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

function renderSkillsPage(client: SkillsClient = createEmptySkillsClient()) {
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
  it('renders header, subtitle, category tabs, searchbox, and toolbar action buttons', async () => {
    renderSkillsPage()

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
  })

  it('renders the 4 showcase cards from ref-12.png in a 2-column grid when API returns empty list', async () => {
    const { container } = renderSkillsPage()

    await screen.findByTestId('skills-heading')
    const grid = container.querySelector('.skills-cards-grid')
    expect(grid).not.toBeNull()

    const gridScope = within(grid as HTMLElement)
    expect(gridScope.getByText('Criar relatórios')).toBeVisible()
    expect(
      gridScope.getByText('Transforma dados e notas em relatórios claros com resumo executivo.'),
    ).toBeVisible()

    expect(gridScope.getByText('Revisar textos')).toBeVisible()
    expect(
      gridScope.getByText('Corrige clareza, tom e ortografia mantendo a sua voz.'),
    ).toBeVisible()

    expect(gridScope.getByText('Analisar planilhas')).toBeVisible()
    expect(
      gridScope.getByText('Encontra padrões, somas e pontos fora da curva em tabelas.'),
    ).toBeVisible()

    expect(gridScope.getByText('Padrão Auryly')).toBeVisible()
    expect(
      gridScope.getByText('Aplica as cores, fontes e tom de voz da marca Auryly.'),
    ).toBeVisible()

    expect(gridScope.getAllByText('Instalada')).toHaveLength(3)
    expect(gridScope.getByText('Criada por você')).toBeVisible()

    const firstCard = grid?.querySelector('.skill-card-v6.is-selected')
    expect(firstCard).not.toBeNull()
    expect(firstCard).toHaveTextContent('Criar relatórios')
  })

  it('renders the right-hand inspector with settings, quick example, and closed instructions accordion by default', async () => {
    const user = userEvent.setup()
    renderSkillsPage()

    const inspector = await screen.findByTestId('skills-inspector-panel')
    const inspectorScope = within(inspector)

    expect(inspectorScope.getByRole('heading', { name: 'Criar relatórios' })).toBeVisible()
    expect(inspectorScope.getByText('Instalada')).toBeVisible()
    expect(
      inspectorScope.getByText('Transforma dados e notas em relatórios claros com resumo executivo.'),
    ).toBeVisible()

    const availabilitySelect = inspectorScope.getByLabelText('Disponível em') as HTMLSelectElement
    expect(availabilitySelect).toBeVisible()
    expect(
      Array.from(availabilitySelect.options).map((opt) => opt.textContent),
    ).toEqual(['Todos os projetos', 'Apenas Auryly'])

    const confirmToggle = inspectorScope.getByRole('checkbox', {
      name: /Pedir confirmação|Antes de publicar/i,
    })
    expect(confirmToggle).toBeChecked()

    await user.selectOptions(availabilitySelect, 'Apenas Auryly')
    expect(availabilitySelect.value).toBe('Apenas Auryly')

    await user.click(confirmToggle)
    expect(confirmToggle).not.toBeChecked()

    const quickExample = inspectorScope.getByTestId('skill-quick-example')
    expect(quickExample).toHaveTextContent('Crie o relatório semanal da Auryly')
    expect(within(quickExample).getByRole('button', { name: 'Testar no chat' })).toBeVisible()

    const accordion = inspectorScope.getByTestId('skill-instructions-accordion')
    const trigger = within(accordion).getByRole('button', { name: /Instruções da habilidade/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(
      within(accordion).queryByText(/Estruture com resumo executivo/i),
    ).not.toBeInTheDocument()

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(
      within(accordion).getByText(/Estruture com resumo executivo/i),
    ).toBeVisible()
  })

  it('updates the inspector and collapses instructions accordion when selecting another card', async () => {
    const user = userEvent.setup()
    const { container } = renderSkillsPage()

    const inspector = await screen.findByTestId('skills-inspector-panel')
    const accordion = within(inspector).getByTestId('skill-instructions-accordion')
    const trigger = within(accordion).getByRole('button', { name: /Instruções da habilidade/i })

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    const grid = container.querySelector('.skills-cards-grid') as HTMLElement
    await user.click(within(grid).getByText('Padrão Auryly'))

    expect(within(inspector).getByRole('heading', { name: 'Padrão Auryly' })).toBeVisible()
    expect(within(inspector).getByText('Criada por você')).toBeVisible()
    expect(
      within(inspector).getByText('Aplica as cores, fontes e tom de voz da marca Auryly.'),
    ).toBeVisible()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('isolates API items from showcase cards when API returns skills', async () => {
    const apiSkill: SkillSummary = {
      id: 'api-skill-1',
      name: 'Custom API Skill',
      description: 'Executes an API-backed workflow.',
      version: '2.1.0',
      tags: ['automation'],
      source: 'custom',
      available: true,
    }
    const client = createEmptySkillsClient({
      list: vi.fn(() => Promise.resolve({ items: [apiSkill], next_cursor: null })),
    })
    const { container } = renderSkillsPage(client)

    expect(await screen.findByRole('link', { name: 'Custom API Skill' })).toBeVisible()
    const grid = container.querySelector('.skills-cards-grid') as HTMLElement
    expect(within(grid).queryByText('Criar relatórios')).not.toBeInTheDocument()
  })
})
