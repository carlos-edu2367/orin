import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { CommandPicker } from '../../src/features/conversations/CommandPicker'

const COMMANDS = [
  { command_id: 'demo:decide', slug: 'decide', plugin_id: 'demo', description: 'Registra uma decisão', argument_hint: '', qualified: false },
]

/** The picker is open for "/dec" in the composer; the other controls stand for the permission card and a dialog. */
function Page({ onSelect, onOther }: { onSelect: (token: string) => void; onOther: () => void }) {
  const [text, setText] = useState('/dec')
  return (
    <div>
      <textarea id="composer-message" aria-label="Mensagem" value={text} onChange={(event) => setText(event.target.value)} />
      <CommandPicker commands={COMMANDS} query="dec" onSelect={onSelect} onDismiss={() => {}} />
      <input aria-label="Outro campo" />
      <button type="button" onClick={onOther}>Sim</button>
    </div>
  )
}

describe('W7-R10 the command picker answers Enter only from the composer', () => {
  it('does not run a command when Enter is pressed on another control', () => {
    const onSelect = vi.fn()
    const onOther = vi.fn()
    render(<Page onSelect={onSelect} onOther={onOther} />)
    const other = screen.getByLabelText('Outro campo')
    other.focus()
    const notPrevented = fireEvent.keyDown(other, { key: 'Enter' })
    expect(onSelect).not.toHaveBeenCalled()
    expect(notPrevented).toBe(true)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Sim' }), { key: 'Enter' })
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('still picks the highlighted command when Enter comes from the composer', () => {
    const onSelect = vi.fn()
    render(<Page onSelect={onSelect} onOther={vi.fn()} />)
    fireEvent.keyDown(screen.getByLabelText('Mensagem'), { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledWith('decide')
  })

  it('ignores Enter while the text is being composed (IME)', () => {
    const onSelect = vi.fn()
    render(<Page onSelect={onSelect} onOther={vi.fn()} />)
    fireEvent.keyDown(screen.getByLabelText('Mensagem'), { key: 'Enter', isComposing: true })
    expect(onSelect).not.toHaveBeenCalled()
  })
})
