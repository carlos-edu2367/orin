import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { CommandPalette } from '../../src/components/CommandPalette'

describe('CommandPalette', () => {
  it('mounts the keyboard-opened history overlay at the document body', async () => {
    render(
      <MemoryRouter>
        <header style={{ backdropFilter: 'blur(18px)' }}>
          <CommandPalette conversations={[{ conversation_id: 'chat-1', title: 'Planejar a semana', state: 'completed' }]} />
        </header>
      </MemoryRouter>,
    )

    await userEvent.setup().keyboard('{Control>}k{/Control}')

    const dialog = await screen.findByRole('dialog', { name: 'Navegação' })
    expect(dialog.parentElement).toBe(document.body)
    expect(screen.getByRole('button', { name: /Planejar a semana/ })).toBeInTheDocument()
  })

  it('searches conversations beyond the compact resting list', async () => {
    const conversations = Array.from({ length: 40 }, (_, index) => ({
      conversation_id: `chat-${index + 1}`,
      title: index === 39 ? 'Investigação antiga' : `Conversa ${index + 1}`,
      state: 'completed',
    }))
    render(<MemoryRouter><CommandPalette conversations={conversations} /></MemoryRouter>)

    await userEvent.setup().keyboard('{Control>}k{/Control}')
    expect(screen.queryByRole('button', { name: /Investigação antiga/ })).not.toBeInTheDocument()
    await userEvent.setup().type(screen.getByRole('textbox', { name: 'Buscar' }), 'investigação antiga')
    expect(screen.getByRole('button', { name: /Investigação antiga/ })).toBeInTheDocument()
  })
})
