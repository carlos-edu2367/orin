import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UserQuestionCard } from '../../src/features/conversations/UserQuestionCard'

describe('UserQuestionCard', () => {
  it('submits checkbox, single-choice and free-text answers as one batch', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<UserQuestionCard active questions={[
      { id: 'features', question: 'Quais recursos?', mode: 'checkbox', options: [{ id: 'search', label: 'Busca' }, { id: 'export', label: 'Exportar' }] },
      { id: 'tone', question: 'Qual tom?', mode: 'single_choice', options: [{ id: 'formal', label: 'Formal' }, { id: 'casual', label: 'Casual' }] },
      { id: 'notes', question: 'Algo mais?', mode: 'text', options: [] },
    ]} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByLabelText('Busca'))
    fireEvent.click(screen.getByLabelText('Casual'))
    fireEvent.change(screen.getByPlaceholderText('Escreva sua resposta (opcional)'), { target: { value: 'Sem preferência adicional.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))

    expect(onSubmit).toHaveBeenCalledWith([
      { id: 'features', selected: ['search'], note: '' },
      { id: 'tone', selected: ['casual'], note: '' },
      { id: 'notes', selected: [], note: 'Sem preferência adicional.' },
    ])
  })
})
