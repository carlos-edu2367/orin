import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { OrchestrationSettingItem } from '../../../api/orchestrationSettings'
import { EffortSettingsPanel } from '../EffortSettingsPanel'

function makeItem(overrides: Partial<OrchestrationSettingItem> & Pick<OrchestrationSettingItem, 'path' | 'type' | 'label'>): OrchestrationSettingItem {
  return {
    group: 'effort',
    description: 'Descrição de teste.',
    scope: 'user_only',
    defaultValue: null,
    value: null,
    layer: 'default',
    userValue: null,
    hasUserOverride: false,
    minimum: null,
    maximum: null,
    choices: null,
    itemType: null,
    valueMinimum: null,
    valueMaximum: null,
    locked: false,
    ...overrides,
  }
}

function sampleEffortItems(overrides: Partial<Record<string, Partial<OrchestrationSettingItem>>> = {}): OrchestrationSettingItem[] {
  const base: OrchestrationSettingItem[] = [
    makeItem({
      path: 'effortLevel',
      type: 'choice',
      label: 'Esforço padrão',
      description: 'Nível global quando não há escolha por modelo ou turno.',
      defaultValue: null,
      value: null,
      choices: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    }),
    makeItem({
      path: 'effort.planMode',
      type: 'choice',
      label: 'Esforço no modo plano',
      description: 'Nível aplicado automaticamente no modo plano.',
      defaultValue: null,
      value: 'high',
      layer: 'user',
      userValue: 'high',
      hasUserOverride: true,
      choices: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    }),
    makeItem({
      path: 'effort.subagentPolicy',
      type: 'choice',
      label: 'Esforço dos subagentes',
      description: 'Define se subagentes usam padrão, herdam ou seguem tabela por tipo.',
      defaultValue: 'model_default',
      value: 'by_type',
      layer: 'user',
      userValue: 'by_type',
      hasUserOverride: true,
      choices: ['model_default', 'inherit', 'by_type'],
    }),
    makeItem({
      path: 'effort.byType',
      type: 'map',
      label: 'Esforço por tipo de subagente',
      description: 'Nível aplicado a cada tipo de subagente quando a política está em by_type.',
      defaultValue: { explore: 'low', plan: 'high', verifier: 'high', general: 'inherit', fork: 'inherit' },
      value: { explore: 'low', plan: 'high', verifier: 'high', general: 'inherit', fork: 'inherit' },
      layer: 'default',
      itemType: 'choice',
      choices: ['inherit', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    }),
    makeItem({
      path: 'effort.maxForSubagents',
      type: 'choice',
      label: 'Teto de esforço para subagentes',
      description: 'Nível máximo permitido para qualquer subagente.',
      scope: 'project_lower_only',
      defaultValue: null,
      value: 'high',
      layer: 'project',
      choices: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    }),
    makeItem({
      path: 'effort.boostKeyword',
      type: 'boolean',
      label: 'Palavra ultrathink eleva um turno',
      description: 'Eleva o esforço quando ultrathink aparece na mensagem.',
      defaultValue: false,
      value: false,
      layer: 'default',
    }),
    makeItem({
      path: 'effort.escalateOnFailure',
      type: 'boolean',
      label: 'Subir esforço após reprovação',
      description: 'Eleva um degrau após reprovação do verificador.',
      defaultValue: false,
      value: true,
      layer: 'managed',
      locked: true,
    }),
  ]

  return base.map((item) => ({ ...item, ...(overrides[item.path] ?? {}) }))
}

describe('EffortSettingsPanel', () => {
  it('renders effort controls, origin badges, byType editor, and saves policy, byType, cap and boostKeyword', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()

    render(<EffortSettingsPanel items={sampleEffortItems()} onSave={onSave} />)

    // Origin badges are displayed
    expect(screen.getAllByText('Padrão').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Usuário').length).toBeGreaterThan(0)
    expect(screen.getByText('Projeto')).toBeInTheDocument()
    expect(screen.getByText('Gerenciado')).toBeInTheDocument()

    // Managed field is disabled and has no reset button
    const escalateRow = screen.getByTestId('effort-setting-effort.escalateOnFailure')
    expect(within(escalateRow).getByRole('switch')).toBeDisabled()
    expect(within(escalateRow).queryByRole('button', { name: /restaurar padrão/i })).not.toBeInTheDocument()

    // Change subagentPolicy
    await user.selectOptions(screen.getByLabelText('Esforço dos subagentes'), 'inherit')
    expect(onSave).toHaveBeenCalledWith({ 'effort.subagentPolicy': 'inherit' })

    // Edit byType table for verifier
    const byTypeEditor = screen.getByTestId('effort-by-type-editor')
    expect(byTypeEditor).toHaveAttribute('data-active', 'true')
    const verifierSelect = screen.getByLabelText(/esforço para verificador/i)
    await user.selectOptions(verifierSelect, 'xhigh')
    expect(onSave).toHaveBeenCalledWith({
      'effort.byType': {
        explore: 'low',
        plan: 'high',
        verifier: 'xhigh',
        general: 'inherit',
        fork: 'inherit',
      },
    })

    // Change maxForSubagents cap
    await user.selectOptions(screen.getByLabelText('Teto de esforço para subagentes'), 'medium')
    expect(onSave).toHaveBeenCalledWith({ 'effort.maxForSubagents': 'medium' })

    // Toggle boostKeyword
    const boostRow = screen.getByTestId('effort-setting-effort.boostKeyword')
    await user.click(within(boostRow).getByRole('switch'))
    expect(onSave).toHaveBeenCalledWith({ 'effort.boostKeyword': true })

    // Reset user override on planMode
    const planRow = screen.getByTestId('effort-setting-effort.planMode')
    await user.click(within(planRow).getByRole('button', { name: /restaurar padrão/i }))
    expect(onSave).toHaveBeenCalledWith({ 'effort.planMode': null })
  })

  it('marks byType editor as inactive with explanatory hint when subagentPolicy is not by_type', () => {
    render(
      <EffortSettingsPanel
        items={sampleEffortItems({
          'effort.subagentPolicy': { value: 'model_default', layer: 'default', hasUserOverride: false },
        })}
        onSave={vi.fn()}
      />,
    )

    const byTypeEditor = screen.getByTestId('effort-by-type-editor')
    expect(byTypeEditor).toHaveAttribute('data-active', 'false')
    expect(byTypeEditor).toHaveTextContent(/ativa quando.*por tipo de subagente/i)
  })
})
