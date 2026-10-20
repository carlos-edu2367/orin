import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from '../../src/features/conversations/Composer'

describe('W7-R12 ctrl+r then Enter sends like the composer does', () => {
  it('sends the recalled text through the composer, and does not route it to onRunCommand', () => {
    const onRunCommand = vi.fn()
    const sent: string[] = []
    function Harness() {
      const [value, setValue] = useState('meu rascunho longo')
      return (
        <Composer value={value} onChange={setValue} onSubmit={() => sent.push(value)} onRunCommand={onRunCommand} conversationMode="code"
          history={['!ls -la']} />
      )
    }
    render(<Harness />)
    const input = screen.getByLabelText('Mensagem')
    input.focus()
    fireEvent.keyDown(input, { key: 'r', ctrlKey: true })
    const search = screen.getByLabelText('Buscar no histórico')
    fireEvent.change(search, { target: { value: 'ls' } })
    fireEvent.keyDown(search, { key: 'Enter' })
    expect(onRunCommand).not.toHaveBeenCalled()
    expect(sent).toEqual(['!ls -la'])
  })

  it('does not send when the recalled text cannot be sent yet (nothing typed)', () => {
    const sent: string[] = []
    function Harness() {
      const [value, setValue] = useState('')
      return <Composer value={value} onChange={setValue} onSubmit={() => sent.push(value)} history={['   ']} />
    }
    render(<Harness />)
    const input = screen.getByLabelText('Mensagem')
    input.focus()
    fireEvent.keyDown(input, { key: 'r', ctrlKey: true })
    fireEvent.keyDown(screen.getByLabelText('Buscar no histórico'), { key: 'Enter' })
    expect(sent).toEqual([])
  })
})
