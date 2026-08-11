import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExecutionControls } from '../../src/features/executions/ExecutionControls'

describe('ExecutionControls command intentions', () => {
  it('emits a pause command once and disables controls while it is pending', async () => {
    const onAction = vi.fn()
    const user = userEvent.setup()

    render(<ExecutionControls state="RUNNING" pending onAction={onAction} />)

    const pause = screen.getByRole('button', { name: 'Pausar execução' })
    expect(pause).toBeDisabled()
    await user.click(pause)
    expect(onAction).not.toHaveBeenCalled()
  })

  it('maps a resume click to the RESUME command', async () => {
    const onAction = vi.fn()
    const user = userEvent.setup()

    render(<ExecutionControls state="PAUSED" onAction={onAction} />)
    await user.click(screen.getByRole('button', { name: 'Retomar execução' }))

    expect(onAction).toHaveBeenCalledOnce()
    expect(onAction).toHaveBeenCalledWith('RESUME')
  })
})
