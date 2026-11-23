import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { KeybindingsProvider } from '../../keybindings/engine'
import { Composer } from '../Composer'

describe('Composer /btw UX and Escape safety', () => {
  it('shows /btw visual mode, dedicated send button while running, and submits /btw instead of queueing', () => {
    const onSubmit = vi.fn()
    const onQueue = vi.fn()
    const onStop = vi.fn()

    render(
      <KeybindingsProvider>
        <Composer
          value="/btw qual era o nome da tabela?"
          onChange={vi.fn()}
          onSubmit={onSubmit}
          onQueue={onQueue}
          onStop={onStop}
          running={true}
          canSend={true}
        />
      </KeybindingsProvider>,
    )

    expect(screen.getByTestId('composer-btw-badge')).toHaveTextContent(/não interrompe o agente/i)
    expect(screen.getByTestId('composer-btw-send')).toBeInTheDocument()

    const textarea = screen.getByLabelText('Mensagem')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onQueue).not.toHaveBeenCalled()
    expect(onStop).not.toHaveBeenCalled()
  })

  it('closes an open /btw card on Escape without cancelling the running main turn', () => {
    const onStop = vi.fn()
    const onDismissBtw = vi.fn()

    const { rerender } = render(
      <KeybindingsProvider>
        <Composer
          value=""
          onChange={vi.fn()}
          onSubmit={vi.fn()}
          onStop={onStop}
          running={true}
          btwOpen={true}
          onDismissBtw={onDismissBtw}
        />
      </KeybindingsProvider>,
    )

    const textarea = screen.getByLabelText('Mensagem')
    fireEvent.keyDown(textarea, { key: 'Escape' })

    expect(onDismissBtw).toHaveBeenCalledTimes(1)
    expect(onStop).not.toHaveBeenCalled()

    rerender(
      <KeybindingsProvider>
        <Composer
          value=""
          onChange={vi.fn()}
          onSubmit={vi.fn()}
          onStop={onStop}
          running={true}
          btwOpen={false}
          onDismissBtw={onDismissBtw}
        />
      </KeybindingsProvider>,
    )

    fireEvent.keyDown(screen.getByLabelText('Mensagem'), { key: 'Escape' })
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('triggers /btw toggle on Alt+B keybinding', () => {
    const onTriggerBtw = vi.fn()
    render(
      <KeybindingsProvider>
        <Composer
          value=""
          onChange={vi.fn()}
          onSubmit={vi.fn()}
          onTriggerBtw={onTriggerBtw}
        />
      </KeybindingsProvider>,
    )

    const textarea = screen.getByLabelText('Mensagem')
    fireEvent.keyDown(textarea, { key: 'b', altKey: true })
    expect(onTriggerBtw).toHaveBeenCalledTimes(1)
  })
})
