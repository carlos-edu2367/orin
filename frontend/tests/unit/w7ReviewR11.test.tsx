import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { CodePermissionDock } from '../../src/features/conversations/CodePermissionDock'
import { Composer } from '../../src/features/conversations/Composer'

describe('W7-R11 Escape closes the history search without denying the permission', () => {
  it('does not resolve the pending permission when Escape closes the ctrl+r search', () => {
    const onResolve = vi.fn()
    function Harness() {
      const [value, setValue] = useState('')
      return (
        <>
          <CodePermissionDock requests={[{ requestId: 'r1', turnId: 't1', toolName: 'run_command', command: 'npm test', state: 'pending' }]} onResolve={onResolve} />
          <Composer value={value} onChange={setValue} onSubmit={vi.fn()} history={['rode os testes']} />
        </>
      )
    }
    render(<Harness />)
    const input = screen.getByLabelText('Mensagem')
    input.focus()
    fireEvent.keyDown(input, { key: 'r', ctrlKey: true })
    const search = screen.getByLabelText('Buscar no histórico')
    fireEvent.keyDown(search, { key: 'Escape' })
    expect(onResolve).not.toHaveBeenCalled()
  })

  it('still denies the permission on Escape when nothing else is open', () => {
    const onResolve = vi.fn()
    render(<CodePermissionDock requests={[{ requestId: 'r1', turnId: 't1', toolName: 'run_command', command: 'npm test', state: 'pending' }]} onResolve={onResolve} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onResolve).toHaveBeenCalledWith('r1', { behavior: 'deny', scope: 'once' })
  })
})
