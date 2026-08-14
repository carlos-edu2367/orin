import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UserQuestionCard } from '../../src/features/conversations/UserQuestionCard'

describe('UserQuestionCard', () => {
  it('shows one question at a time and submits the complete batch at the end', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<UserQuestionCard active questions={[
      { id: 'features', question: 'Quais recursos?', mode: 'checkbox', options: [{ id: 'search', label: 'Busca' }, { id: 'export', label: 'Exportar' }] },
      { id: 'tone', question: 'Qual tom?', mode: 'single_choice', options: [{ id: 'formal', label: 'Formal' }, { id: 'casual', label: 'Casual' }] },
      { id: 'notes', question: 'Algo mais?', mode: 'text', options: [] },
    ]} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByLabelText('Busca'))
    fireEvent.click(screen.getByRole('button', { name: 'Continuar →' }))
    expect(screen.getByText('Qual tom?')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Escreva sua resposta (opcional)')).not.toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Casual'))
    fireEvent.click(screen.getByRole('button', { name: 'Continuar →' }))
    fireEvent.change(screen.getByPlaceholderText('Escreva sua resposta (opcional)'), { target: { value: 'Sem preferência adicional.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith([
      { id: 'features', selected: ['search'], note: '' },
      { id: 'tone', selected: ['casual'], note: '' },
      { id: 'notes', selected: [], note: 'Sem preferência adicional.' },
    ]))
  })

  it('allows editing an earlier answer before continuing', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<UserQuestionCard active questions={[
      { id: 'first', question: 'Primeira?', mode: 'single_choice', options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }] },
      { id: 'second', question: 'Segunda?', mode: 'text', options: [] },
    ]} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByLabelText('A'))
    fireEvent.click(screen.getByRole('button', { name: 'Continuar →' }))
    fireEvent.click(screen.getByRole('button', { name: /Editar resposta/ }))

    expect(screen.getByLabelText('A')).toBeChecked()
    expect(screen.queryByText('Segunda?')).not.toBeInTheDocument()
  })
})
