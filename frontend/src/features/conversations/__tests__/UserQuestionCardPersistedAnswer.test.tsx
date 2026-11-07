import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UserQuestionCard, parseAnsweredText } from '../UserQuestionCard'
import type { UserQuestion } from '../activityTypes'

const questions: UserQuestion[] = [
  {
    id: 'deploy',
    question: 'Posso fazer o deploy das duas edge functions?',
    mode: 'single_choice',
    placeholder: '',
    options: [
      { id: 'yes', label: 'Pode aplicar tudo' },
      { id: 'no', label: 'Ainda não' },
    ],
  },
  { id: 'notes', question: 'Algo mais?', mode: 'text', placeholder: '', options: [] },
]

const answerText = [
  'Respostas às perguntas do agente:',
  '',
  'Posso fazer o deploy das duas edge functions?',
  'Seleção: Pode aplicar tudo',
  '',
  'Algo mais?',
  'Seleção: nenhuma',
  'Observação: avise quando terminar',
].join('\n')

describe('the answer to an AskUserQuestion shows once, in its card', () => {
  it('recovers the answers from the persisted message', () => {
    expect(parseAnsweredText(answerText, questions)).toEqual({
      deploy: { id: 'deploy', selected: ['yes'], note: '' },
      notes: { id: 'notes', selected: [], note: 'avise quando terminar' },
    })
  })

  it('shows the persisted answer in a closed card after a reload', () => {
    render(<UserQuestionCard questions={questions} active={false} onSubmit={vi.fn()} answeredText={answerText} />)
    expect(screen.getByText('Pode aplicar tudo')).toBeInTheDocument()
    expect(screen.getByText('Obs.: avise quando terminar')).toBeInTheDocument()
  })
})
