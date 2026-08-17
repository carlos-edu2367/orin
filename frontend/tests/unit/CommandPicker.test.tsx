import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CommandPicker } from '../../src/features/conversations/CommandPicker'

const COMMANDS = [
  { command_id: 'demo:daily', slug: 'daily', plugin_id: 'demo', description: 'Nota diária', argument_hint: '[data]', qualified: false },
  { command_id: 'demo:decide', slug: 'decide', plugin_id: 'demo', description: 'Registra uma decisão', argument_hint: '', qualified: false },
  { command_id: 'alpha:daily', slug: 'daily', plugin_id: 'alpha', description: 'Outra', argument_hint: '', qualified: true },
]

describe('CommandPicker', () => {
  it('filters by the typed prefix', () => {
    render(<CommandPicker commands={COMMANDS} query="dec" onSelect={() => {}} onDismiss={() => {}} />)

    expect(screen.getByRole('option', { name: /decide/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /Nota diária/ })).not.toBeInTheDocument()
  })

  it('shows the qualified form for an ambiguous slug', () => {
    render(<CommandPicker commands={COMMANDS} query="" onSelect={() => {}} onDismiss={() => {}} />)

    expect(screen.getByRole('option', { name: /alpha:daily/ })).toBeInTheDocument()
  })

  it('selects the highlighted command on Enter', async () => {
    const onSelect = vi.fn()
    render(<CommandPicker commands={COMMANDS} query="dec" onSelect={onSelect} onDismiss={() => {}} />)

    await userEvent.keyboard('{Enter}')

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ command_id: 'demo:decide' }))
  })

  it('moves the highlight with the arrow keys', async () => {
    const onSelect = vi.fn()
    render(<CommandPicker commands={COMMANDS} query="" onSelect={onSelect} onDismiss={() => {}} />)

    await userEvent.keyboard('{ArrowDown}{Enter}')

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].command_id).not.toBe(COMMANDS[0].command_id)
  })

  it('dismisses on Escape', async () => {
    const onDismiss = vi.fn()
    render(<CommandPicker commands={COMMANDS} query="" onSelect={() => {}} onDismiss={onDismiss} />)

    await userEvent.keyboard('{Escape}')

    expect(onDismiss).toHaveBeenCalled()
  })

  it('renders nothing when the query matches no command', () => {
    const { container } = render(<CommandPicker commands={COMMANDS} query="zzz" onSelect={() => {}} onDismiss={() => {}} />)

    expect(container).toBeEmptyDOMElement()
  })
})
