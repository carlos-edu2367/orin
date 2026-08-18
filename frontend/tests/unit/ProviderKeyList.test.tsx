import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ProviderKeyList } from '../../src/features/providers/ProviderKeyList'
import type { ProviderApiKeyState } from '../../src/api/providers'

function keys(): ProviderApiKeyState[] {
  return [
    { id: 1, label: 'conta free 1', position: 0, status: 'active', cooldownUntil: null },
    { id: 2, label: 'conta paga', position: 1, status: 'cooldown', cooldownUntil: '2099-01-01T00:00:00Z' },
  ]
}

const noop = () => {}

describe('ProviderKeyList', () => {
  it('labels the first key as principal and shows the cooldown status of the second', () => {
    render(<ProviderKeyList keys={keys()} pending={false} cooldownSeconds={60} onAdd={noop} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={noop} />)

    expect(screen.getByText('conta free 1')).toBeInTheDocument()
    expect(screen.getByText('Principal')).toBeInTheDocument()
    expect(screen.getByText(/Em cooldown/)).toBeInTheDocument()
  })

  it('submits the new key and label, then clears the input', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<ProviderKeyList keys={[]} pending={false} cooldownSeconds={60} onAdd={onAdd} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={noop} />)

    await user.type(screen.getByLabelText('Nova chave'), 'sk-second-key')
    await user.type(screen.getByLabelText('Apelido (opcional)'), 'conta paga')
    await user.click(screen.getByRole('button', { name: 'Adicionar chave' }))

    expect(onAdd).toHaveBeenCalledWith('sk-second-key', 'conta paga')
  })

  it('disables moving the first key up and the last key down', () => {
    render(<ProviderKeyList keys={keys()} pending={false} cooldownSeconds={60} onAdd={noop} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={noop} />)

    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]).getByRole('button', { name: 'Mover para cima' })).toBeDisabled()
    expect(within(rows[1]).getByRole('button', { name: 'Mover para baixo' })).toBeDisabled()
  })

  it('submits a new cooldown value', async () => {
    const user = userEvent.setup()
    const onCooldownSecondsChange = vi.fn()
    render(<ProviderKeyList keys={keys()} pending={false} cooldownSeconds={60} onAdd={noop} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={onCooldownSecondsChange} />)

    const input = screen.getByLabelText('Tempo de cooldown (s)')
    await user.clear(input)
    await user.type(input, '120')
    await user.click(screen.getByRole('button', { name: 'Salvar tempo de cooldown' }))

    expect(onCooldownSecondsChange).toHaveBeenCalledWith(120)
  })
})
