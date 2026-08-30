import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from '../../src/features/conversations/Composer'

const COMMANDS = [
  { command_id: 'demo:daily', slug: 'daily', plugin_id: 'demo', description: 'Nota diária', argument_hint: '[data]', qualified: false },
]

describe('Composer command picker', () => {
  it('opens the picker when / starts an empty composer', async () => {
    render(<Composer value="/" onChange={() => {}} onSubmit={() => {}} commands={COMMANDS} />)

    expect(await screen.findByRole('listbox', { name: /Comandos de plugin/ })).toBeInTheDocument()
  })

  it('does not open the picker for a slash inside existing text', () => {
    render(<Composer value="veja /usr/local" onChange={() => {}} onSubmit={() => {}} commands={COMMANDS} />)

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('keeps Enter-to-send working when the picker is closed', async () => {
    const onSubmit = vi.fn()
    render(<Composer value="olá" onChange={() => {}} onSubmit={onSubmit} commands={COMMANDS} />)

    await userEvent.type(screen.getByLabelText('Mensagem'), '{Enter}')

    expect(onSubmit).toHaveBeenCalled()
  })

  it('does not send when Enter picks a command', async () => {
    const onSubmit = vi.fn()
    const onChange = vi.fn()
    render(<Composer value="/dai" onChange={onChange} onSubmit={onSubmit} commands={COMMANDS} />)

    await userEvent.keyboard('{Enter}')

    expect(onSubmit).not.toHaveBeenCalled()
    expect(onChange).toHaveBeenCalledWith('/daily ')
  })

  it('lets the user explicitly toggle Code mode with an announced pressed state', async () => {
    const onCodeModeChange = vi.fn()
    const user = userEvent.setup()
    render(<Composer value="implemente isto" onChange={() => {}} onSubmit={() => {}} codeMode="auto" onCodeModeChange={onCodeModeChange} />)

    const toggle = screen.getByRole('button', { name: 'Ativar Modo Code' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)

    expect(onCodeModeChange).toHaveBeenCalledWith('code')
  })
})
