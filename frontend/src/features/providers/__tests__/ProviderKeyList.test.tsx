import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ProviderApiKeyState } from '../../../api/providers'
import { ProviderKeyList } from '../ProviderKeyList'

const noop = () => {}
const noopAdd = async () => false

function sampleKeys(): ProviderApiKeyState[] {
  return [
    { id: 10, label: 'NVIDIA Trial 1', position: 0, status: 'exhausted', cooldownUntil: null },
    { id: 20, label: 'NVIDIA Trial 2', position: 1, status: 'cooldown', cooldownUntil: '2099-01-01T00:00:00Z' },
    { id: 30, label: 'NVIDIA Trial 3', position: 2, status: 'active', cooldownUntil: null },
  ]
}

describe('ProviderKeyList exhausted & reactivation UI', () => {
  it('renders "Sem saldo / Esgotada" badge when key.status === "exhausted"', () => {
    render(
      <ProviderKeyList
        keys={sampleKeys()}
        pending={false}
        cooldownSeconds={60}
        onAdd={noopAdd}
        onRename={noop}
        onRemove={noop}
        onMoveUp={noop}
        onMoveDown={noop}
        onCooldownSecondsChange={noop}
      />,
    )

    const items = screen.getAllByRole('listitem')
    expect(within(items[0]).getByRole('status')).toHaveTextContent('Sem saldo / Esgotada')
    expect(within(items[1]).getByRole('status')).toHaveTextContent(/Em cooldown/)
    expect(within(items[2]).getByRole('status')).toHaveTextContent('Ativa')
  })

  it('renders "Reativar" button for exhausted and cooldown keys (not active) and triggers onActivate(key.id)', async () => {
    const user = userEvent.setup()
    const onActivate = vi.fn()

    render(
      <ProviderKeyList
        keys={sampleKeys()}
        pending={false}
        cooldownSeconds={60}
        onAdd={noopAdd}
        onRename={noop}
        onRemove={noop}
        onMoveUp={noop}
        onMoveDown={noop}
        onCooldownSecondsChange={noop}
        onActivate={onActivate}
      />,
    )

    const items = screen.getAllByRole('listitem')
    const reactivateExhausted = within(items[0]).getByRole('button', { name: 'Reativar' })
    const reactivateCooldown = within(items[1]).getByRole('button', { name: 'Reativar' })
    expect(within(items[2]).queryByRole('button', { name: 'Reativar' })).toBeNull()

    await user.click(reactivateExhausted)
    expect(onActivate).toHaveBeenCalledWith(10)

    await user.click(reactivateCooldown)
    expect(onActivate).toHaveBeenCalledWith(20)
  })

  it('disables "Reativar" buttons while pending is true to prevent concurrent duplicate activations', () => {
    const onActivate = vi.fn()

    render(
      <ProviderKeyList
        keys={sampleKeys()}
        pending={true}
        cooldownSeconds={60}
        onAdd={noopAdd}
        onRename={noop}
        onRemove={noop}
        onMoveUp={noop}
        onMoveDown={noop}
        onCooldownSecondsChange={noop}
        onActivate={onActivate}
      />,
    )

    const items = screen.getAllByRole('listitem')
    expect(within(items[0]).getByRole('button', { name: 'Reativar' })).toBeDisabled()
    expect(within(items[1]).getByRole('button', { name: 'Reativar' })).toBeDisabled()
  })
})
