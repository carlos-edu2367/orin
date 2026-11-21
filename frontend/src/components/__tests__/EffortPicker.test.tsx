import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EffortPicker } from '../EffortPicker'

describe('EffortPicker', () => {
  it('hides completely when the model reports no supported effort levels', () => {
    const { container } = render(
      <EffortPicker
        supportedEfforts={[]}
        defaultEffort={null}
        value={null}
        onChange={vi.fn()}
        boost={false}
        onToggleBoost={vi.fn()}
      />,
    )

    expect(container.firstChild).toBeNull()
    expect(screen.queryByTestId('effort-picker-trigger')).not.toBeInTheDocument()
    expect(screen.queryByTestId('effort-boost-button')).not.toBeInTheDocument()
  })

  it('lists only the levels accepted by the current model and marks the model default', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()

    render(
      <EffortPicker
        supportedEfforts={['low', 'medium', 'high', 'xhigh']}
        defaultEffort="medium"
        value={null}
        onChange={onChange}
      />,
    )

    const trigger = screen.getByTestId('effort-picker-trigger')
    expect(trigger).toHaveTextContent(/médio/i)
    await user.click(trigger)

    const listbox = screen.getByRole('listbox', { name: /nível de esforço/i })
    const options = within(listbox).getAllByRole('option')
    expect(options).toHaveLength(4)
    expect(options.map((opt) => opt.getAttribute('data-effort'))).toEqual(['low', 'medium', 'high', 'xhigh'])

    const defaultOption = options.find((opt) => opt.getAttribute('data-effort') === 'medium')
    expect(defaultOption).toHaveAttribute('data-default', 'true')
    expect(defaultOption).toHaveTextContent(/padrão/i)

    expect(within(listbox).queryByText(/ultra/i)).not.toBeInTheDocument()
    expect(within(listbox).queryByText(/máximo/i)).not.toBeInTheDocument()

    await user.click(options[2])
    expect(onChange).toHaveBeenCalledWith('high')
  })

  it('allows resetting an explicit choice back to the model default', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()

    render(
      <EffortPicker
        supportedEfforts={['low', 'medium', 'high']}
        defaultEffort="medium"
        value="high"
        onChange={onChange}
      />,
    )

    await user.click(screen.getByTestId('effort-picker-trigger'))
    const resetButton = screen.getByRole('button', { name: /usar padrão do modelo/i })
    await user.click(resetButton)

    expect(onChange).toHaveBeenCalledWith(null)
  })

  it('renders and toggles the "Pensar mais" single-turn boost button without emojis', async () => {
    const user = userEvent.setup()
    const onToggleBoost = vi.fn()

    const { rerender } = render(
      <EffortPicker
        supportedEfforts={['low', 'medium', 'high', 'ultra']}
        defaultEffort="medium"
        value="medium"
        onChange={vi.fn()}
        boost={false}
        onToggleBoost={onToggleBoost}
      />,
    )

    const boostButton = screen.getByTestId('effort-boost-button')
    expect(boostButton).toHaveTextContent('Pensar mais')
    expect(boostButton).toHaveAttribute('aria-pressed', 'false')

    await user.click(boostButton)
    expect(onToggleBoost).toHaveBeenCalledTimes(1)

    rerender(
      <EffortPicker
        supportedEfforts={['low', 'medium', 'high', 'ultra']}
        defaultEffort="medium"
        value="medium"
        onChange={vi.fn()}
        boost={true}
        onToggleBoost={onToggleBoost}
      />,
    )
    expect(screen.getByTestId('effort-boost-button')).toHaveAttribute('aria-pressed', 'true')
  })
})
