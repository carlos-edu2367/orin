import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { UserQuestionCard } from '../UserQuestionCard'
import type { UserQuestion } from '../activityTypes'

const QUESTION: UserQuestion = {
  id: 'q1',
  question: 'Which database?',
  header: 'Database',
  mode: 'single_choice',
  options: [
    { id: 'o1', label: 'SQLite', description: 'Local file, no server', preview: "engine = create_engine('sqlite:///app.db')" },
    { id: 'o2', label: 'Postgres', description: 'Needs a server' },
  ],
}

describe('UserQuestionCard: header, descriptions and previews', () => {
  it('shows the header chip and each option description', () => {
    render(<UserQuestionCard questions={[QUESTION]} active onSubmit={vi.fn().mockResolvedValue(undefined)} />)
    expect(screen.getByText('Database')).toBeTruthy()
    expect(screen.getByText('Local file, no server')).toBeTruthy()
    expect(screen.getByText('Needs a server')).toBeTruthy()
    expect(screen.queryByTestId('user-question-preview')).toBeNull()
  })

  it('shows the preview of the chosen option', () => {
    render(<UserQuestionCard questions={[QUESTION]} active onSubmit={vi.fn().mockResolvedValue(undefined)} />)
    fireEvent.click(screen.getByLabelText(/SQLite/))
    expect(screen.getByTestId('user-question-preview').textContent).toContain("create_engine('sqlite:///app.db')")
    fireEvent.click(screen.getByLabelText(/Postgres/))
    expect(screen.queryByTestId('user-question-preview')).toBeNull()
  })

  it('parses header, description and preview from the activity event', () => {
    const event = parseConversationActivityEvent(
      {
        event_id: 'evt-question-1',
        event_type: 'tool.finished',
        turn_id: 'turn-question-1',
        agent_id: 'agent:main',
        summary: 'Aguardando resposta para 1 pergunta',
        payload: {
          tool_name: 'ask_user', tool_kind: 'user_input', status: 'succeeded', wait_for_user: true,
          questions: [{ ...QUESTION, placeholder: '' }],
        },
      },
      'a.1',
    )
    const parsed = event?.questions?.[0]
    expect(parsed?.header).toBe('Database')
    expect(parsed?.options[0].description).toBe('Local file, no server')
    expect(parsed?.options[0].preview).toContain('sqlite')
  })
})
