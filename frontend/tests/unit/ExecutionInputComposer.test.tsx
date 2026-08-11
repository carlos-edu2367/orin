import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExecutionInputComposer } from '../../src/features/executions/ExecutionInputComposer'

describe('ExecutionInputComposer', () => {
  it('submits the opaque input reference supplied by the user', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<ExecutionInputComposer pending={false} onSubmit={onSubmit} />)

    await user.click(screen.getByRole('button', { name: 'Fornecer referência de entrada' }))
    await user.type(screen.getByLabelText('Referência de entrada'), 'input:approved-user-reply')
    await user.click(screen.getByRole('button', { name: 'Enviar entrada' }))

    expect(onSubmit).toHaveBeenCalledWith('input:approved-user-reply')
  })
})
